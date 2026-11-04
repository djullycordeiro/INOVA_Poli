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
