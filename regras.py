"""
regras.py — as contas da JustPlan. Nada de HTML aqui: só recebe dados e devolve resultados.
"""

TAXA_MENSAL = 0.01  # custo do dinheiro: 1% ao mês


# ---------------------------------------------------------------------------
# Formatação (padrão brasileiro)
# ---------------------------------------------------------------------------
def brl(valor):
    """1234.5 -> 'R$ 1.234,50'"""
    if valor is None:
        return "—"
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-R$ " if valor < 0 else "R$ ") + texto


def numero(valor):
    """3300.0 -> '3.300'; 2.5 -> '2,5'"""
    if valor == int(valor):
        return f"{int(valor):,}".replace(",", ".")
    return str(valor).replace(".", ",")


# Unidades por extenso (singular, plural). As que não estão aqui ficam como abreviação: cx, un, kg, L, m.
UNIDADES = {
    "rolo": ("rolo", "rolos"), "kit": ("kit", "kits"), "par": ("par", "pares"),
    "sc": ("saco", "sacos"), "mil": ("milheiro", "milheiros"), "bb": ("bombona", "bombonas"),
    "cil": ("cilindro", "cilindros"), "fr": ("frasco", "frascos"), "fd": ("fardo", "fardos"),
    "bd": ("balde", "baldes"), "pct": ("pacote", "pacotes"), "unidade": ("un", "un"),
}


def quantidade(qtd, unidade):
    """(81, 'rolo') -> '81 rolos'; (1, 'sc') -> '1 saco'; (40, 'cx') -> '40 cx'"""
    singular, plural = UNIDADES.get(unidade, (unidade, unidade))
    return f"{numero(qtd)} {singular if qtd == 1 else plural}"


def data_br(iso, ano_curto=True):
    """'2025-10-09' -> '09/10/25'"""
    a, m, d = iso[:10].split("-")
    return f"{d}/{m}/{a[2:] if ano_curto else a}"


def prazo_pagamento(dias):
    return "à vista" if not dias else f"{int(dias)} dias"


def prazo_entrega(dias):
    if not dias:
        return "mesmo dia"
    return "1 dia" if dias == 1 else f"{int(dias)} dias"


# ---------------------------------------------------------------------------
# Cotações
# ---------------------------------------------------------------------------
def custo_de_hoje(custo_total, prazo_dias):
    """Traz o valor a pagar no futuro para o valor de hoje.
    Pagar daqui a 60 dias deixa o dinheiro rendendo 2 meses no caixa."""
    return custo_total / (1 + TAXA_MENSAL) ** ((prazo_dias or 0) / 30)


def analisar_cotacao(compra, propostas):
    """Compara as propostas de um pedido e devolve:
    - as propostas com o custo de hoje recalculado e marcadas (melhor / escolhida)
    - o veredito: 'certa', 'cara' ou 'pressa', e quanto a escolha custou a mais
    """
    linhas = []
    for p in propostas:
        total = p["preco_unit"] * p["quantidade"] + (p["frete"] or 0)
        linhas.append({
            "fornecedor": p["fornecedor"],
            "preco_unit": p["preco_unit"],
            "frete": p["frete"] or 0,
            "prazo_pagamento_dias": p["prazo_pagamento_dias"],
            "prazo_entrega_dias": p["prazo_entrega_dias"],
            "custo_total": total,
            "custo_hoje": custo_de_hoje(total, p["prazo_pagamento_dias"]),
            "escolhida": p["vencedora"] == "Sim",
        })

    melhor = min(linhas, key=lambda l: l["custo_hoje"])
    escolhida = next((l for l in linhas if l["escolhida"]), melhor)
    for l in linhas:
        l["melhor"] = l is melhor

    diferenca = round(escolhida["custo_hoje"] - melhor["custo_hoje"], 2)
    if len(linhas) == 1:
        veredito = "unica"
    elif diferenca <= 0:
        veredito = "certa"
    elif compra["tipo_compra"] == "Emergencial":
        veredito = "pressa"
    else:
        veredito = "cara"

    return {"propostas": linhas, "melhor": melhor, "escolhida": escolhida,
            "diferenca": max(diferenca, 0), "veredito": veredito}


# ---------------------------------------------------------------------------
# Nota da Compra
# ---------------------------------------------------------------------------
# Quantas vezes um item precisa aparecer no período para o consumo ser "previsível"
COMPRAS_PARA_PREVISIVEL = 6

CORES_NOTA = {
    "v": {"rotulo": "Boa decisão", "plural": "verdes", "resumo": "boa decisão"},
    "a": {"rotulo": "Atenção", "plural": "amarelas", "resumo": "atenção"},
    "r": {"rotulo": "Decisão ruim", "plural": "vermelhas", "resumo": "decisão ruim"},
}


def cor_da_nota(nota):
    """Verde a partir de 75, amarela de 50 a 74, vermelha abaixo de 50."""
    if nota >= 75:
        return "v"
    return "a" if nota >= 50 else "r"


def variacao_preco(compra):
    """Quanto o preço pago ficou acima (+) ou abaixo (−) do preço médio planejado. None se não há referência."""
    ref = compra["preco_medio_planejado_item"]
    if not ref:
        return None
    return compra["preco_unit_final"] / ref - 1


def nota_da_compra(compra, analise, vezes_que_o_item_foi_comprado):
    """Calcula a nota (0 a 100) e devolve os motivos, cada um com sua cor:
    'g' = ponto positivo, 'a' = atenção, 'r' = problema."""
    nota = 100
    motivos = []

    # 1. Preço comparado com o histórico do item
    var = variacao_preco(compra)
    if var is not None:
        pct = round(abs(var) * 100)
        if var > 0.10:
            nota -= 40
            motivos.append(("r", f"Pagou {pct}% acima do preço médio planejado do item"))
        elif var >= 0.03:
            nota -= 15
            motivos.append(("a", f"Pagou {pct}% acima do preço médio planejado do item"))
        elif var <= -0.03:
            motivos.append(("g", f"Pagou {pct}% abaixo do preço médio planejado"))
        else:
            motivos.append(("g", "Preço dentro do histórico do item"))

    # 2. Ignorou a proposta com melhor custo de hoje
    if analise["veredito"] in ("cara", "pressa"):
        nota -= 30
        if analise["veredito"] == "pressa":
            motivos.append(("r", f"Havia proposta {brl(analise['diferenca'])} mais barata, mas com entrega em "
                                 f"{prazo_entrega(analise['melhor']['prazo_entrega_dias'])}"))
        else:
            motivos.append(("r", f"Havia proposta {brl(analise['diferenca'])} mais barata no custo de hoje (frete + prazo)"))

    # 3. Quantos fornecedores foram cotados
    k = int(compra["qtd_cotacoes"])
    if k == 1:
        nota -= 20
        motivos.append(("r", "Só 1 fornecedor cotado"))
    elif k == 2:
        nota -= 5
        motivos.append(("a", "Só 2 fornecedores cotados"))
    else:
        motivos.append(("g", f"{k} fornecedores cotados"))

    # 4. Emergencial
    previsivel = vezes_que_o_item_foi_comprado >= COMPRAS_PARA_PREVISIVEL
    if compra["tipo_compra"] == "Emergencial":
        nota -= 15
        motivos.append(("r", "Compra emergencial de item com consumo previsível" if previsivel else "Compra emergencial"))

    # 5. Orçamento
    if compra["previsto_no_orcamento"] == "Não":
        nota -= 5
        motivos.append(("a", "Fora do orçamento"))

    # Ponto positivo: negociação
    if compra["saving_negociacao"]:
        motivos.append(("g", f"Negociação economizou {brl(compra['saving_negociacao'])}"))

    nota = max(nota, 0)
    return {"nota": nota, "cor": cor_da_nota(nota), "motivos": motivos,
            "dica": dica_para_proxima(compra, analise, k)}


def dica_para_proxima(compra, analise, qtd_cotacoes):
    """Uma dica fixa, escolhida pelo problema mais importante da compra."""
    if compra["tipo_compra"] == "Emergencial":
        return ("Esse item se repete ao longo do ano. Deixe o Radar avisar antes de faltar "
                "e faça um pedido planejado, com cotação e prazo.")
    if analise["veredito"] == "cara":
        return ("Compare as propostas pelo custo de hoje (preço + frete + prazo de pagamento), "
                "não só pelo preço unitário.")
    if qtd_cotacoes < 3:
        return "Cote com pelo menos 3 fornecedores: mais concorrência dá mais poder de negociação."
    return "Continue assim: cotar com 3 ou mais fornecedores e comparar pelo custo de hoje."


# ---------------------------------------------------------------------------
# Histórico de preços
# ---------------------------------------------------------------------------
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Período móvel: os 12 meses que terminam no mês da compra mais recente.
# definir_periodo() é chamada a cada página (no app.py) e atualiza as variáveis abaixo.
# ---------------------------------------------------------------------------
NOMES_MES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
NOMES_MES_EXTENSO = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                     "agosto", "setembro", "outubro", "novembro", "dezembro"]

# Valores iniciais (o período dos dados do pitch); são recalculados por definir_periodo()
INICIO_PERIODO, FIM_PERIODO = date(2025, 10, 1), date(2026, 9, 30)
MESES = ["out", "nov", "dez", "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set"]
MESES_EXTENSO = ["outubro", "novembro", "dezembro", "janeiro", "fevereiro", "março",
                 "abril", "maio", "junho", "julho", "agosto", "setembro"]
DATA_DE_HOJE = date(2026, 10, 1)


def definir_periodo(ultima_compra, tem_compra_do_site):
    """ultima_compra: data (date) do pedido mais recente.
    O período vai do 1º dia de 11 meses antes até o último dia do mês da última compra.
    'Hoje' é a data real se já existe compra lançada pelo site; senão, o dia seguinte aos dados
    (para a demonstração do pitch continuar fazendo sentido)."""
    global INICIO_PERIODO, FIM_PERIODO, MESES, MESES_EXTENSO, DATA_DE_HOJE
    ano, mes = ultima_compra.year, ultima_compra.month
    ano_ini, mes_ini = (ano, mes - 11) if mes > 11 else (ano - 1, mes + 1)
    INICIO_PERIODO = date(ano_ini, mes_ini, 1)
    proximo = date(ano + (mes == 12), mes % 12 + 1, 1)
    FIM_PERIODO = proximo - timedelta(days=1)
    indices = [(mes_ini - 1 + i) % 12 for i in range(12)]
    MESES = [NOMES_MES[i] for i in indices]
    MESES_EXTENSO = [NOMES_MES_EXTENSO[i] for i in indices]
    DATA_DE_HOJE = date.today() if tem_compra_do_site else ultima_compra + timedelta(days=1)


def periodo_texto():
    """'out/2025 a set/2026'"""
    return f"{MESES[0]}/{INICIO_PERIODO.year} a {MESES[-1]}/{FIM_PERIODO.year}"


def _mes_indice(iso):
    """Posição do mês dentro do período: 0 = primeiro mês ... 11 = último."""
    return (int(iso[:4]) - INICIO_PERIODO.year) * 12 + int(iso[5:7]) - INICIO_PERIODO.month


def nome_curto(item):
    """'Luvas nitrílicas (cx c/ 100 un)' -> 'Luvas nitrílicas'"""
    return item.split(" (")[0]


def historico_do_item(compras):
    """Recebe as compras de UM item (qualquer ordem) e devolve o resumo e os pontos do gráfico.
    Cada ponto tem x e y em % para ser posicionado com CSS (0% = esquerda/topo)."""
    compras = sorted(compras, key=lambda c: c["data_pedido"])
    ref = compras[-1]["preco_medio_planejado_item"]
    emerg = [c for c in compras if c["tipo_compra"] == "Emergencial"]
    media_emerg = sum(c["preco_unit_final"] for c in emerg) / len(emerg) if emerg else None

    precos = [c["preco_unit_final"] for c in compras] + ([ref] if ref else [])
    baixo, alto = min(precos) * 0.9, max(precos) * 1.08   # folga em cima e embaixo do gráfico
    altura = lambda v: (1 - (v - baixo) / (alto - baixo)) * 100
    dias_periodo = (FIM_PERIODO - INICIO_PERIODO).days

    pontos = []
    for c in compras:
        dia = date.fromisoformat(c["data_pedido"])
        pontos.append({
            "compra": c,
            "x": (dia - INICIO_PERIODO).days / dias_periodo * 100,
            "y": altura(c["preco_unit_final"]),
            "emergencial": c["tipo_compra"] == "Emergencial",
            "rotulo": f"{dia:%d/%m} · {brl(c['preco_unit_final'])}",
            "vs": (c["preco_unit_final"] / ref - 1) if ref else None,
        })

    # 4 linhas de grade com o valor ao lado
    grade = []
    for k in range(4):
        v = baixo + (alto - baixo) * (k + 0.5) / 4
        grade.append({"y": altura(v), "texto": brl(v)})

    return {
        "item": compras[0]["item"], "unidade": compras[0]["unidade"],
        "ref": ref, "ref_y": altura(ref) if ref else None,
        "n": len(compras), "n_emerg": len(emerg),
        "media_emerg": media_emerg,
        "pct_emerg": (media_emerg / ref - 1) if (ref and media_emerg) else None,
        "pago_a_mais": sum(c["pago_a_mais_vs_planejado"] or 0 for c in emerg),
        "pontos": pontos, "grade": grade, "meses": MESES,
    }


def por_extenso(unidade):
    """'cx' -> 'caixa' (para 'preço por caixa')"""
    extenso = {"cx": "caixa", "un": "unidade", "kg": "kg", "L": "litro", "m": "metro"}
    return extenso.get(unidade, UNIDADES.get(unidade, (unidade,))[0])


# ---------------------------------------------------------------------------
# Savings
# ---------------------------------------------------------------------------
CATEGORIA_NO_TEXTO = {  # como cada categoria aparece numa frase
    "Embalagens": "embalagens", "Peças de Manutenção": "peças de manutenção",
    "Frota e Logística": "frota", "EPIs e Uniformes": "EPIs",
    "Insumos e Condimentos": "insumos", "Limpeza e Sanitização": "limpeza",
    "Material de Laboratório": "material de laboratório", "Material de Escritório e TI": "escritório e TI",
}


def lista_em_texto(itens):
    """['a', 'b', 'c'] -> 'a, b e c'"""
    itens = list(itens)
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


def fracao_em_texto(f):
    """0.48 -> 'quase metade'"""
    for limite, texto in [(0.70, "a maior parte"), (0.55, "mais da metade"), (0.45, "quase metade"),
                          (0.30, "cerca de um terço"), (0.20, "cerca de um quarto")]:
        if f >= limite:
            return texto
    return f"{f:.0%}".replace(".", ",")


def desconto_por_cotacoes(compras):
    """Desconto médio na negociação por número de cotações (só compras planejadas)."""
    resultado = []
    for k in (1, 2, 3, 4):
        grupo = [c for c in compras if c["tipo_compra"] == "Planejada" and int(c["qtd_cotacoes"]) == k]
        descontos = [(c["saving_negociacao"] or 0) / (c["preco_unit_inicial"] * c["quantidade"]) for c in grupo]
        resultado.append({"k": k, "pct": sum(descontos) / len(descontos) if descontos else None})
    return resultado


def savings(compras):
    """Economia na negociação x perda nas emergenciais, no total, por mês, por categoria
    e por quantidade de cotações. Também escreve as frases de 'deu certo' e 'deu errado'."""
    economia = sum(c["saving_negociacao"] or 0 for c in compras)
    emerg = [c for c in compras if c["tipo_compra"] == "Emergencial"]
    perda = sum(c["pago_a_mais_vs_planejado"] or 0 for c in emerg)

    # Mês a mês (índice 0 = primeiro mês do período ... 11 = último)
    meses = [{"nome": MESES[i], "extenso": MESES_EXTENSO[i], "economia": 0.0, "perda": 0.0, "cats_perda": {}} for i in range(12)]
    for c in compras:
        m = meses[_mes_indice(c["data_pedido"])]
        m["economia"] += c["saving_negociacao"] or 0
        if c["tipo_compra"] == "Emergencial":
            m["perda"] += c["pago_a_mais_vs_planejado"] or 0
            m["cats_perda"][c["categoria"]] = m["cats_perda"].get(c["categoria"], 0) + (c["pago_a_mais_vs_planejado"] or 0)
    teto = max([m["economia"] for m in meses] + [m["perda"] for m in meses] + [1])
    for m in meses:
        m["h_economia"] = m["economia"] / teto * 100
        m["h_perda"] = m["perda"] / teto * 100

    # Por categoria
    cats = {}
    for c in compras:
        x = cats.setdefault(c["categoria"], {"categoria": c["categoria"], "n": 0, "economia": 0.0, "perda": 0.0})
        x["n"] += 1
        x["economia"] += c["saving_negociacao"] or 0
        if c["tipo_compra"] == "Emergencial":
            x["perda"] += c["pago_a_mais_vs_planejado"] or 0
    categorias = sorted(cats.values(), key=lambda x: x["economia"], reverse=True)

    # Frases
    melhores = sorted(meses, key=lambda m: m["economia"], reverse=True)[:2]
    melhores = [m for m in meses if m in melhores]  # na ordem do calendário
    texto_certo = (f"{lista_em_texto(m['extenso'] for m in melhores).capitalize()} tiveram a maior economia na negociação do ano."
                   if economia else "")
    pior = max(meses, key=lambda m: m["perda"])
    texto_pior = ""
    if pior["perda"]:
        cats_pior = sorted(pior["cats_perda"], key=pior["cats_perda"].get, reverse=True)
        texto_pior = (f"{pior['extenso'].capitalize()} foi o pior mês: {brl(round(pior['perda']))[:-3]} pagos a mais em compras "
                      f"emergenciais de {lista_em_texto(CATEGORIA_NO_TEXTO.get(x, x.lower()) for x in cats_pior)}.")
    pior_cat = max(categorias, key=lambda x: x["perda"]) if categorias else None
    texto_cat = ""
    if pior_cat and perda:
        texto_cat = (f"{pior_cat['categoria']} concentra{'m' if pior_cat['categoria'].endswith('s') else ''} "
                     f"{fracao_em_texto(pior_cat['perda'] / perda)} da perda do ano ({brl(pior_cat['perda'])}).")

    return {
        "economia": economia, "perda": perda, "saldo": economia - perda,
        "perda_por_real": perda / economia if economia else None,
        "meses": meses, "categorias": categorias,
        "texto_certo": texto_certo, "texto_pior": texto_pior, "texto_cat": texto_cat,
    }


def pct(valor, casas=1):
    """0.0516 -> '5,2%'"""
    return "—" if valor is None else f"{valor * 100:.{casas}f}%".replace(".", ",")


# ---------------------------------------------------------------------------
# Radar de Emergenciais
# ---------------------------------------------------------------------------
# "Hoje" do Radar = DATA_DE_HOJE, definida em definir_periodo() (lá em cima).
VEZES_DIAS_ALERTA_AREA = 2          # área em alerta: taxa de emergenciais acima de 2× a mediana
DIAS_JANELA_SAZONAL = 45            # emergências de um item todas dentro de 45 dias = sazonal


def _mediana(valores):
    v = sorted(valores)
    meio = len(v) // 2
    return (v[meio] if len(v) % 2 else (v[meio - 1] + v[meio]) / 2) if v else 0


def _minusculo(nome):
    """'Bandeja EPS para cortes de frango' -> 'bandeja EPS para cortes de frango' (mantém siglas)"""
    return nome[:1].lower() + nome[1:] if nome[1:2].islower() else nome


def radar(compras, todas, area_usuario=None):
    """compras = as que o usuário pode ver; todas = a empresa inteira (para medianas e consumo previsível).
    area_usuario = None para gerente."""
    vezes = {}
    for c in todas:
        vezes[c["codigo_item"]] = vezes.get(c["codigo_item"], 0) + 1
    previsivel = lambda cod: vezes.get(cod, 0) >= COMPRAS_PARA_PREVISIVEL
    emerg = [c for c in compras if c["tipo_compra"] == "Emergencial"]

    numeros = {
        "n": len(emerg),
        "pct": len(emerg) / len(compras) if compras else 0,
        "perda": sum(c["pago_a_mais_vs_planejado"] or 0 for c in emerg),
        "um_fornecedor": sum(int(c["qtd_cotacoes"]) == 1 for c in emerg),
        "fora_orcamento": sum(c["previsto_no_orcamento"] == "Não" for c in emerg),
    }
    alertas = []

    # --- Alerta de CATEGORIA: emergências recorrentes em itens de consumo previsível -------------
    meses_prev = {}
    for c in emerg:
        if previsivel(c["codigo_item"]):
            meses_prev.setdefault(c["categoria"], set()).add(_mes_indice(c["data_pedido"]))
    if meses_prev:
        cat = max(meses_prev, key=lambda k: (len(meses_prev[k]), sum(
            c["pago_a_mais_vs_planejado"] or 0 for c in emerg if c["categoria"] == k)))
        da_cat = [c for c in emerg if c["categoria"] == cat]
        por_mes = [0] * 12
        for c in da_cat:
            por_mes[_mes_indice(c["data_pedido"])] += 1
        meses_com = sum(1 for n in por_mes if n)
        if meses_com >= 4:
            itens = []
            for c in sorted(da_cat, key=lambda c: c["data_pedido"]):
                nome = _nome_bem_curto(c["item"])
                if nome not in itens:
                    itens.append(nome)
            alertas.append({
                "tipo": "Categoria",
                "titulo": f"“{cat} apresenta comportamento recorrente de compras emergenciais.”",
                "texto": f"Compras emergenciais em {meses_com} dos últimos 12 meses: {lista_em_texto(itens)}. "
                         "São itens de consumo previsível." if all(previsivel(c["codigo_item"]) for c in da_cat) else
                         f"Compras emergenciais em {meses_com} dos últimos 12 meses: {lista_em_texto(itens)}. "
                         "A maioria é de consumo previsível.",
                "grafico": "meses",
                "meses": [{"nome": MESES[i], "n": n} for i, n in enumerate(por_mes)],
            })

    # --- Alerta de ÁREA: taxa de emergenciais muito acima da mediana das áreas ------------------
    taxas = {}
    for c in todas:
        t = taxas.setdefault(c["area_solicitante"], [0, 0])
        t[0] += c["tipo_compra"] == "Emergencial"
        t[1] += 1
    taxas = {a: e / n for a, (e, n) in taxas.items()}
    mediana = _mediana(list(taxas.values()))
    acima = sorted([a for a, t in taxas.items() if t > VEZES_DIAS_ALERTA_AREA * mediana], key=taxas.get, reverse=True)
    if area_usuario:
        acima = [a for a in acima if a == area_usuario]
    if acima:
        area = acima[0]
        contagem = {}
        for c in emerg:
            if c["area_solicitante"] == area:
                contagem[c["item"]] = contagem.get(c["item"], 0) + 1
        top = sorted(contagem, key=lambda k: (contagem[k], -len(k)), reverse=True)[:3]
        vezes_mediana = int(taxas[area] / mediana) if mediana else 0
        texto = (f"{pct(taxas[area])} dos pedidos da área são emergenciais, mais de {vezes_mediana} vezes "
                 f"a mediana das áreas ({pct(mediana)}). Os itens que mais se repetem são "
                 f"{lista_em_texto(_minusculo(nome_curto(i)) for i in top)}.")
        if not area_usuario and len(acima) > 1:
            texto += f" {acima[1]} também está acima do padrão: {pct(taxas[acima[1]])}."
        visiveis = taxas if not area_usuario else {area_usuario: taxas[area_usuario]}
        teto = max(taxas.values()) or 1
        alertas.append({
            "tipo": "Área",
            "titulo": f"“{area} realiza compras emergenciais com frequência superior ao padrão.”",
            "texto": texto, "grafico": "areas", "mediana": pct(mediana),
            "areas": [{"nome": a, "pct": pct(t), "largura": t / teto * 100, "alta": t > VEZES_DIAS_ALERTA_AREA * mediana}
                      for a, t in sorted(visiveis.items(), key=lambda kv: kv[1], reverse=True)],
        })

    # --- Alerta de ITEM: item de consumo previsível comprado repetidamente às pressas -----------
    por_item = {}
    for c in compras:
        por_item.setdefault(c["codigo_item"], []).append(c)
    candidatos = [(cod, lst) for cod, lst in por_item.items() if previsivel(cod)
                  and sum(x["tipo_compra"] == "Emergencial" for x in lst) >= 3]
    item_alerta = None
    if candidatos:
        cod, lst = max(candidatos, key=lambda kv: sum(x["tipo_compra"] == "Emergencial" for x in kv[1]))
        h = historico_do_item(lst)
        lst = sorted(lst, key=lambda c: c["data_pedido"])
        intervalo = _intervalo_medio(lst)
        area_item = max({c["area_solicitante"] for c in lst}, key=lambda a: sum(c["area_solicitante"] == a for c in lst))
        nome = nome_curto(h["item"])
        item_alerta = cod
        alertas.append({
            "tipo": "Item",
            "titulo": f"“{nome} apresenta{'m' if nome.split()[0].endswith('s') else ''} consumo previsível, "
                      f"mas vê{'m' if nome.split()[0].endswith('s') else ''} sendo adquirid{'as' if nome.split()[0].endswith('as') else 'os' if nome.split()[0].endswith('s') else 'o'} repetidamente por meio emergencial.”",
            "texto": f"{_area_curta(area_item)} compra {_minusculo(nome)} a cada ~{intervalo} dias, mas {h['n_emerg']} das {h['n']} "
                     f"compras foram emergenciais, pagando em média {pct(h['pct_emerg'], 0)} a mais: {brl(h['pago_a_mais'])} no ano.",
            "grafico": "compras",
            "pontos": [{"x": p["x"], "emergencial": p["emergencial"], "rotulo": p["rotulo"]} for p in h["pontos"]],
        })

    return {"numeros": numeros, "alertas": alertas,
            "prevencao": _prevencao(compras, todas, previsivel, item_alerta, area_usuario)}


def _nome_bem_curto(item):
    """'Caixa de papelão ondulado 20 kg' -> 'caixa de papelão'; 'Bandeja EPS para cortes...' -> 'bandeja EPS'"""
    nome = nome_curto(item)
    for corte in (" para ", " 20 ", " 50 "):
        nome = nome.split(corte)[0]
    palavras = nome.split()
    if len(palavras) > 3:
        nome = " ".join(palavras[:3])
    return _minusculo(nome)


def _intervalo_medio(compras_ordenadas):
    datas = [date.fromisoformat(c["data_pedido"]) for c in compras_ordenadas]
    if len(datas) < 2:
        return None
    return round((datas[-1] - datas[0]).days / (len(datas) - 1))


def _area_curta(area):
    """'Produção (Abate e Cortes)' -> 'A Produção'"""
    nome = area.split(" (")[0]
    artigo = "A" if nome.split()[0].endswith(("ção", "ística", "dade", "a")) else "O"
    return f"{artigo} {nome}"


def _data_br_longa(d):
    return f"{d:%d/%m}"


def _prevencao(compras, todas, previsivel, item_alerta, area_usuario):
    """Cartões 'Antes que aconteça de novo'. Cada um vira uma requisição se a pessoa clicar."""
    cartoes = []
    hoje = DATA_DE_HOJE
    emerg = [c for c in compras if c["tipo_compra"] == "Emergencial"]

    # 1. Sazonal: todas as emergências de um item caíram numa janela curta do ano
    por_item = {}
    for c in emerg:
        por_item.setdefault(c["codigo_item"], []).append(c)
    for cod, lst in por_item.items():
        datas = sorted(date.fromisoformat(c["data_pedido"]) for c in lst)
        if len(datas) >= 3 and (datas[-1] - datas[0]).days <= DIAS_JANELA_SAZONAL:
            extra = sum(c["pago_a_mais_vs_planejado"] or 0 for c in lst)
            ref = lst[0]["preco_medio_planejado_item"]
            media = sum(c["preco_unit_final"] for c in lst) / len(lst)
            # Pedido planejado ~25 dias antes da primeira emergência, no ano seguinte
            limite = date(datas[0].year + 1, datas[0].month, 1) - timedelta(days=17)
            meses_txt = lista_em_texto(sorted({MESES[_mes_indice(d.isoformat())] for d in datas}, key=MESES.index))
            evento = "o Natal deste ano" if datas[0].month in (11, 12) else "o mesmo período deste ano"
            cartoes.append({
                "chave": f"planejada:{cod}", "tipo": "planejada", "itens": cod, "area": lst[0]["area_solicitante"],
                "tag": f"Sazonal · {'Natal ' + str(datas[0].year + 1) if datas[0].month in (11, 12) else 'recorrente'}",
                "titulo": nome_curto(lst[0]["item"]).capitalize() if False else lst[0]["item"].split(" (")[0],
                "texto": f"Em {meses_txt}/{str(datas[0].year)[2:]} foram {len(lst)} compras emergenciais, "
                         f"{pct(media / ref - 1, 0) if ref else ''} acima do preço planejado: {brl(extra)} a mais. "
                         f"Abra a requisição planejada até {_data_br_longa(limite)} para {evento}.",
                "data_limite": limite.isoformat(), "acao": "Criar requisição planejada",
            })

    # 2. Consumo previsível: próxima reposição do item em alerta
    if item_alerta:
        lst = sorted([c for c in compras if c["codigo_item"] == item_alerta], key=lambda c: c["data_pedido"])
        intervalo = _intervalo_medio(lst)
        proxima = date.fromisoformat(lst[-1]["data_pedido"]) + timedelta(days=intervalo)
        entregas = [c["prazo_entrega_dias"] for c in lst if c["tipo_compra"] == "Planejada"]
        prazo = round(sum(entregas) / len(entregas)) if entregas else 10
        limite = proxima - timedelta(days=prazo)
        quando = "nesta semana" if (limite - hoje).days <= 7 else f"até {_data_br_longa(limite)}"
        if limite < hoje:
            quando = "imediatamente (já está atrasado)"
        cartoes.append({
            "chave": f"planejada:{item_alerta}", "tipo": "planejada", "itens": item_alerta, "area": lst[0]["area_solicitante"],
            "tag": "Consumo previsível", "titulo": lst[0]["item"],
            "texto": f"No ritmo de uma compra a cada {intervalo} dias, a próxima reposição é por volta de "
                     f"{_data_br_longa(proxima)}. Com cerca de {prazo} dias de entrega, o pedido planejado precisa sair {quando}.",
            "data_limite": limite.isoformat(), "acao": "Criar requisição planejada",
        })

    # 3. Peças críticas: peças de manutenção que quebraram mais de uma vez sem estoque
    pecas = {}
    for c in emerg:
        if c["categoria"] == "Peças de Manutenção":
            p = pecas.setdefault(c["codigo_item"], {"nome": c["item"].split(" (")[0], "n": 0, "extra": 0.0, "area": c["area_solicitante"]})
            p["n"] += 1
            p["extra"] += c["pago_a_mais_vs_planejado"] or 0
    criticas = sorted([(k, v) for k, v in pecas.items() if v["n"] >= 2], key=lambda kv: (kv[1]["n"], kv[1]["extra"]), reverse=True)[:3]
    if criticas:
        area = criticas[0][1]["area"]
        partes = [f"{v['nome']} ({v['n']} emergências no ano)" if i == 0 else f"{_minusculo(v['nome'])} ({v['n']})"
                  for i, (k, v) in enumerate(criticas)]
        cartoes.append({
            "chave": "estoque:" + ",".join(k for k, _ in criticas), "tipo": "estoque_minimo",
            "itens": ",".join(k for k, _ in criticas), "area": area,
            "tag": "Peças críticas", "titulo": f"Estoque mínimo na {area.split(' (')[0].replace(' Industrial', '')}",
            "texto": f"{lista_em_texto(partes)} quebraram sem peça em estoque. Sugestão: manter estoque mínimo dessas peças.",
            "data_limite": None, "acao": "Definir estoque mínimo",
        })
    return cartoes


# ---------------------------------------------------------------------------
# Planos (preço da JustPlan)
# ---------------------------------------------------------------------------
MENSALIDADE = 1490          # R$ por mês
TAXA_SUCESSO = 0.10         # 10% da economia comprovada
OPCOES_GASTO = [5, 10, 20, 50]      # gasto anual com compras, em R$ milhões
OPCOES_ECONOMIA = [1, 2, 3, 5]      # economia gerada, em %


def simular_plano(gasto_milhoes, economia_pct):
    """Quanto a JustPlan recebe e quanto a cliente fica, num ano."""
    economia = gasto_milhoes * 1_000_000 * economia_pct / 100
    mensalidades = MENSALIDADE * 12
    justplan = mensalidades + economia * TAXA_SUCESSO
    return {
        "economia": economia, "mensalidades": mensalidades, "justplan": justplan,
        "cliente": economia - justplan, "retorno": economia / justplan,
    }


def reais(valor):
    """Valor arredondado, sem centavos: 37880 -> 'R$ 37.880'"""
    return brl(round(valor))[:-3]


# ---------------------------------------------------------------------------
# Propostas de uma requisição (antes de virar compra)
# ---------------------------------------------------------------------------
COTACOES_RECOMENDADAS = 3


def comparar_propostas(propostas, quantidade, data_necessaria, hoje):
    """Calcula custo total e custo de hoje de cada proposta, marca a mais barata
    e avisa quais chegam depois da data necessária."""
    precisa = date.fromisoformat(data_necessaria) if data_necessaria else None
    linhas = []
    for p in propostas:
        total = p["preco_unit"] * quantidade + (p["frete"] or 0)
        chegada = hoje + timedelta(days=p["prazo_entrega_dias"])
        atraso = (chegada - precisa).days if precisa else 0
        linhas.append({"p": p, "custo_total": total,
                       "custo_hoje": custo_de_hoje(total, p["prazo_pagamento_dias"]),
                       "chegada": chegada, "atraso": max(atraso, 0)})
    no_prazo = [l for l in linhas if not l["atraso"]]
    melhor = min(no_prazo or linhas, key=lambda l: l["custo_hoje"]) if linhas and quantidade else None
    mais_barata = min(linhas, key=lambda l: l["custo_hoje"]) if linhas and quantidade else None
    for l in linhas:
        l["melhor"] = l is melhor
    return {"linhas": sorted(linhas, key=lambda l: l["custo_hoje"]), "melhor": melhor,
            "mais_barata_atrasa": bool(mais_barata and mais_barata is not melhor),
            "mais_barata": mais_barata, "faltam": max(COTACOES_RECOMENDADAS - len(linhas), 0)}


# ---------------------------------------------------------------------------
# Registrar compra: transforma requisição + propostas em uma compra com todos os campos
# ---------------------------------------------------------------------------
def montar_compra(id_compra, requisicao, item, propostas, vencedora_id, preco_final, no_orcamento,
                  comprador, precos_planejados, hoje):
    """Devolve (compra, cotacoes) prontas para gravar.
    precos_planejados = preços finais das compras planejadas anteriores desse item (para o preço de referência)."""
    qtd = requisicao["quantidade"]
    venc = next(p for p in propostas if p["id"] == vencedora_id)
    valor_itens = preco_final * qtd
    custo_total = valor_itens + (venc["frete"] or 0)
    ref = round(sum(precos_planejados) / len(precos_planejados), 2) if precos_planejados else preco_final
    emergencial = requisicao["urgencia"] == "Emergencial"

    compra = {
        "id_compra": id_compra, "data_pedido": hoje.isoformat(),
        "area_solicitante": requisicao["area"], "categoria": item["categoria"],
        "codigo_item": item["codigo_item"], "item": item["item"], "unidade": item["unidade"],
        "quantidade": qtd, "fornecedor": venc["fornecedor"], "comprador": comprador,
        "qtd_cotacoes": len(propostas), "tipo_compra": requisicao["urgencia"],
        "motivo_emergencia": (requisicao["justificativa"] or None) if emergencial else None,
        "preco_unit_inicial": venc["preco_unit"], "preco_unit_final": preco_final,
        "valor_itens": round(valor_itens, 2), "frete": venc["frete"] or 0, "custo_total": round(custo_total, 2),
        "prazo_pagamento_dias": venc["prazo_pagamento_dias"],
        "custo_total_hoje": round(custo_de_hoje(custo_total, venc["prazo_pagamento_dias"]), 2),
        "saving_negociacao": round(max(venc["preco_unit"] - preco_final, 0) * qtd, 2),
        "preco_medio_planejado_item": ref,
        "pago_a_mais_vs_planejado": round(max(preco_final - ref, 0) * qtd, 2) if emergencial else 0,
        "previsto_no_orcamento": "Sim" if no_orcamento else "Não",
        "prazo_entrega_dias": venc["prazo_entrega_dias"],
        "origem": "site", "requisicao_id": requisicao["id"],
    }

    # As propostas viram as cotações oficiais da compra (a vencedora com o preço negociado)
    cotacoes = []
    for p in propostas:
        preco = preco_final if p["id"] == vencedora_id else p["preco_unit"]
        total = preco * qtd + (p["frete"] or 0)
        cotacoes.append({
            "id_compra": id_compra, "fornecedor": p["fornecedor"], "preco_unit": preco, "quantidade": qtd,
            "valor_itens": round(preco * qtd, 2), "frete": p["frete"] or 0, "custo_total": round(total, 2),
            "prazo_pagamento_dias": p["prazo_pagamento_dias"],
            "custo_total_hoje": round(custo_de_hoje(total, p["prazo_pagamento_dias"]), 2),
            "prazo_entrega_dias": p["prazo_entrega_dias"], "vencedora": "Sim" if p["id"] == vencedora_id else "Não",
        })
    menor = min(c["custo_total_hoje"] for c in cotacoes)
    for c in cotacoes:
        c["melhor_custo_hoje"] = "Sim" if c["custo_total_hoje"] == menor else "Não"
    return compra, cotacoes
