"""
importacao.py — lê uma planilha de compras (.xlsx ou .csv), confere linha por linha
e calcula os campos que a JustPlan precisa e a empresa normalmente não tem.

Fluxo:
    1. ler_planilha(bytes, nome)  -> linhas cruas de "compras" e (opcional) "cotacoes"
    2. preparar(linhas, ...)      -> compras prontas + lista de erros por linha (prévia)
    3. o app.py grava no banco depois que o gerente confirma

O nome das colunas pode variar: "Data", "Data do pedido" e "data_pedido" são a mesma coluna.
"""

import csv
import io
import re
import unicodedata
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

import regras

# ---------------------------------------------------------------------------
# Colunas aceitas: nome interno -> (obrigatória?, apelidos que a planilha pode usar, explicação)
# ---------------------------------------------------------------------------
COLUNAS_COMPRA = {
    "data_pedido":          (True,  ["data", "data do pedido", "data pedido", "dt pedido", "emissao", "data emissao"], "Data do pedido (dd/mm/aaaa)"),
    "area_solicitante":     (True,  ["area", "area solicitante", "setor", "departamento", "centro de custo"], "Área que pediu"),
    "item":                 (True,  ["item", "descricao", "descricao do item", "produto", "material"], "Descrição do item"),
    "quantidade":           (True,  ["quantidade", "qtd", "qtde", "quant"], "Quantidade comprada"),
    "fornecedor":           (True,  ["fornecedor", "fornecedor vencedor", "razao social", "vendedor"], "Fornecedor escolhido"),
    "preco_unit_final":     (True,  ["preco unitario", "preco unit", "preco final", "valor unitario", "preco", "preco pago"], "Preço unitário pago (depois da negociação)"),
    "tipo_compra":          (True,  ["tipo", "tipo de compra", "tipo compra", "urgencia", "emergencial"], "Planejada ou Emergencial"),
    "qtd_cotacoes":         (False, ["cotacoes", "qtd cotacoes", "numero de cotacoes", "n cotacoes", "propostas"], "Quantos fornecedores foram cotados (padrão: 1)"),
    "frete":                (False, ["frete", "valor frete", "valor do frete"], "Valor do frete (padrão: 0)"),
    "prazo_pagamento_dias": (False, ["prazo pagamento", "prazo de pagamento", "condicao de pagamento", "pagamento dias", "prazo pgto"], "Dias para pagar (0 = à vista)"),
    "prazo_entrega_dias":   (False, ["prazo entrega", "prazo de entrega", "entrega dias", "lead time"], "Dias para entregar"),
    "preco_unit_inicial":   (False, ["preco inicial", "primeira proposta", "preco proposta", "preco antes da negociacao"], "Preço da 1ª proposta, antes de negociar (padrão: igual ao final)"),
    "previsto_no_orcamento":(False, ["orcamento", "previsto no orcamento", "no orcamento", "orcado"], "Sim ou Não (padrão: Sim)"),
    "categoria":            (False, ["categoria", "grupo", "familia", "classe"], "Categoria do item (padrão: Outros)"),
    "unidade":              (False, ["unidade", "un", "unid", "unidade de medida", "um"], "Unidade (cx, un, kg...)"),
    "codigo_item":          (False, ["codigo", "codigo do item", "cod item", "sku", "codigo item"], "Código do item (se não tiver, a JustPlan cria)"),
    "id_compra":            (False, ["pedido", "numero do pedido", "numero pedido", "n pedido", "no pedido", "n do pedido", "id compra", "id", "oc", "n oc", "no oc", "ordem de compra"], "Número do pedido (se não tiver, a JustPlan cria)"),
    "comprador":            (False, ["comprador", "responsavel", "analista"], "Quem comprou"),
    "motivo_emergencia":    (False, ["motivo", "motivo emergencia", "justificativa"], "Por que foi emergencial"),
}

COLUNAS_COTACAO = {
    "id_compra":            (True,  ["pedido", "numero do pedido", "numero pedido", "n pedido", "no pedido", "n do pedido", "id compra", "id", "oc", "n oc", "no oc", "ordem de compra"], "Número do pedido (o mesmo da aba compras)"),
    "fornecedor":           (True,  ["fornecedor", "razao social"], "Fornecedor que mandou a proposta"),
    "preco_unit":           (True,  ["preco unitario", "preco unit", "preco", "valor unitario"], "Preço unitário proposto"),
    "frete":                (False, ["frete", "valor frete"], "Frete (padrão: 0)"),
    "prazo_pagamento_dias": (False, ["prazo pagamento", "prazo de pagamento", "pagamento dias", "prazo pgto"], "Dias para pagar"),
    "prazo_entrega_dias":   (False, ["prazo entrega", "prazo de entrega", "entrega dias", "lead time"], "Dias para entregar"),
}

LIMITE_LINHAS = 20000


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def normalizar(texto):
    """'Preço Unitário (R$)' -> 'preco unitario r'"""
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def _mapear_cabecalho(cabecalho, colunas):
    """Descobre qual coluna da planilha é cada campo. Devolve {campo: índice} e as colunas ignoradas."""
    nomes = [normalizar(c) for c in cabecalho]
    mapa = {}
    for campo, (_, apelidos, _) in colunas.items():
        candidatos = [normalizar(campo)] + [normalizar(a) for a in apelidos]
        for i, nome in enumerate(nomes):
            if i not in mapa.values() and nome in candidatos:
                mapa[campo] = i
                break
    ignoradas = [cabecalho[i] for i in range(len(cabecalho)) if i not in mapa.values() and cabecalho[i]]
    return mapa, ignoradas


def _linhas_xlsx(conteudo):
    livro = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    abas = {}
    for aba in livro.worksheets:
        linhas = [list(l) for l in aba.iter_rows(values_only=True)]
        linhas = [l for l in linhas if any(v not in (None, "") for v in l)]
        if linhas:
            abas[normalizar(aba.title)] = linhas
    return abas


def _linhas_csv(conteudo):
    for codificacao in ("utf-8-sig", "latin-1"):
        try:
            texto = conteudo.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialeto = csv.Sniffer().sniff(texto[:5000], delimiters=";,\t")
    except csv.Error:
        dialeto = csv.excel
    linhas = [l for l in csv.reader(io.StringIO(texto), dialeto) if any(v.strip() for v in l)]
    return {"compras": linhas}


def ler_planilha(conteudo, nome_arquivo):
    """Devolve {'compras': [linhas], 'cotacoes': [linhas] ou None}. A 1ª linha de cada é o cabeçalho."""
    nome = nome_arquivo.lower()
    if nome.endswith(".xlsx"):
        abas = _linhas_xlsx(conteudo)
    elif nome.endswith(".csv"):
        abas = _linhas_csv(conteudo)
    else:
        raise ValueError("Envie um arquivo .xlsx (Excel) ou .csv.")
    if not abas:
        raise ValueError("A planilha está vazia.")
    compras = abas.get("compras") or next(iter(abas.values()))
    cotacoes = abas.get("cotacoes")
    if len(compras) - 1 > LIMITE_LINHAS:
        raise ValueError(f"A planilha tem mais de {LIMITE_LINHAS} linhas. Divida em arquivos menores.")
    return {"compras": compras, "cotacoes": cotacoes}


# ---------------------------------------------------------------------------
# Conversão de valores (aceita o jeito brasileiro de escrever)
# ---------------------------------------------------------------------------
def _numero(valor):
    """'R$ 1.234,56' -> 1234.56; 12 -> 12.0; '' -> None. Erro -> ValueError."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace("R$", "").replace(" ", "").replace("%", "")
    if not texto or texto in ("-", "—"):
        return None
    if "," in texto:                                   # 1.234,56 -> 1234.56
        texto = texto.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", texto):  # 1.200 ou 12.500 = milhar no jeito brasileiro
        texto = texto.replace(".", "")
    return float(texto)


def _data(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor or "").strip()[:10]
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    raise ValueError


def _tipo(valor):
    texto = normalizar(valor)
    if texto in ("emergencial", "emergencia", "urgente", "e", "sim", "s", "1", "urgencia"):
        return "Emergencial"
    if texto in ("planejada", "planejado", "normal", "p", "nao", "n", "0", "programada", "regular"):
        return "Planejada"
    raise ValueError


def _sim_nao(valor, padrao="Sim"):
    texto = normalizar(valor)
    if not texto:
        return padrao
    if texto in ("sim", "s", "1", "yes", "y", "verdadeiro", "true", "x"):
        return "Sim"
    if texto in ("nao", "n", "0", "no", "falso", "false"):
        return "Não"
    raise ValueError


UNIDADES_NO_NOME = {"cx": "cx", "caixa": "cx", "un": "un", "unidade": "un", "kg": "kg", "rolo": "rolo",
                    "saco": "sc", "sc": "sc", "milheiro": "mil", "bombona": "bb", "fardo": "fd", "balde": "bd",
                    "pacote": "pct", "pct": "pct", "frasco": "fr", "kit": "kit", "par": "par", "m": "m", "l": "L",
                    "cilindro": "cil", "litro": "L", "metro": "m"}


def unidade_do_nome(item):
    """'Luvas nitrílicas (cx c/ 100 un)' -> 'cx'; 'Filme stretch (rolo)' -> 'rolo'; sem parênteses -> 'un'"""
    achado = re.search(r"\(([^)]+)\)", item or "")
    if achado:
        primeira = normalizar(achado.group(1)).split(" ")[0]
        return UNIDADES_NO_NOME.get(primeira, "un")
    return "un"


def _codigo_novo(item, usados):
    """'Luvas nitrílicas (cx)' -> 'LUV-001' (sem repetir códigos existentes)."""
    prefixo = (normalizar(item).replace(" ", "")[:3] or "ITM").upper()
    n = 1
    while f"{prefixo}-{n:03d}" in usados:
        n += 1
    usados.add(f"{prefixo}-{n:03d}")
    return f"{prefixo}-{n:03d}"


# ---------------------------------------------------------------------------
# Preparação: confere cada linha e calcula os campos que faltam
# ---------------------------------------------------------------------------
def preparar(abas, compras_existentes, ultimo_numero):
    """compras_existentes = compras já na base (para códigos de item e preço de referência).
    Devolve um dicionário com compras, cotacoes, erros, avisos e o mapeamento de colunas."""
    cab, *linhas = abas["compras"]
    mapa, ignoradas = _mapear_cabecalho(cab, COLUNAS_COMPRA)
    faltando = [COLUNAS_COMPRA[c][2] for c, (obrig, _, _) in COLUNAS_COMPRA.items() if obrig and c not in mapa]
    resultado = {"compras": [], "cotacoes": [], "erros": [], "avisos": [], "ignoradas": ignoradas,
                 "reconhecidas": {campo: cab[i] for campo, i in mapa.items()}, "faltando": faltando}
    if faltando:
        return resultado

    codigos = {normalizar(c["item"]): c["codigo_item"] for c in compras_existentes}
    infos = {c["codigo_item"]: c for c in compras_existentes}
    usados = set(codigos.values())
    ids_existentes = {c["id_compra"] for c in compras_existentes}
    numero = ultimo_numero

    def valor(linha, campo):
        i = mapa.get(campo)
        return linha[i] if i is not None and i < len(linha) else None

    for n_linha, linha in enumerate(linhas, start=2):
        erros = []

        def pega(campo, conversor, padrao=None, obrigatorio=False):
            bruto = valor(linha, campo)
            if bruto in (None, ""):
                if obrigatorio:
                    erros.append(f"{COLUNAS_COMPRA[campo][2]} está vazio")
                return padrao
            try:
                convertido = conversor(bruto)
                return padrao if convertido is None else convertido
            except (ValueError, TypeError):
                erros.append(f"{COLUNAS_COMPRA[campo][2]}: “{bruto}” não foi entendido")
                return padrao

        texto = lambda v: str(v).strip()
        data_pedido = pega("data_pedido", _data, obrigatorio=True)
        area = pega("area_solicitante", texto, obrigatorio=True)
        item = pega("item", texto, obrigatorio=True)
        qtd = pega("quantidade", _numero, obrigatorio=True)
        fornecedor = pega("fornecedor", texto, obrigatorio=True)
        final = pega("preco_unit_final", _numero, obrigatorio=True)
        tipo = pega("tipo_compra", _tipo, obrigatorio=True)
        cotacoes = pega("qtd_cotacoes", _numero, 1)
        frete = pega("frete", _numero, 0)
        pagamento = pega("prazo_pagamento_dias", _numero, 0)
        entrega = pega("prazo_entrega_dias", _numero, None)
        inicial = pega("preco_unit_inicial", _numero, final)
        orcamento = pega("previsto_no_orcamento", _sim_nao, "Sim")
        id_compra = pega("id_compra", texto, None)

        if qtd is not None and qtd <= 0:
            erros.append("Quantidade precisa ser maior que zero")
        if final is not None and final <= 0:
            erros.append("Preço unitário precisa ser maior que zero")
        if cotacoes is not None and cotacoes < 1:
            erros.append("Número de cotações precisa ser pelo menos 1")
        if id_compra and (id_compra in ids_existentes or any(c["id_compra"] == id_compra for c in resultado["compras"])):
            erros.append(f"O pedido {id_compra} já existe")
        if erros:
            resultado["erros"].append({"linha": n_linha, "problemas": erros})
            continue

        chave_item = normalizar(item)
        codigo = pega("codigo_item", texto, None) or codigos.get(chave_item) or _codigo_novo(item, usados)
        codigos.setdefault(chave_item, codigo)
        info = infos.get(codigo, {})
        if not id_compra:
            numero += 1
            id_compra = f"PC-{numero}"
        valor_itens = final * qtd
        custo_total = valor_itens + frete
        resultado["compras"].append({
            "id_compra": id_compra, "data_pedido": data_pedido.isoformat(), "area_solicitante": area,
            "categoria": pega("categoria", texto, None) or (info["categoria"] if info else "Outros"),
            "codigo_item": codigo, "item": item,
            "unidade": pega("unidade", texto, None) or (info["unidade"] if info else unidade_do_nome(item)),
            "quantidade": qtd, "fornecedor": fornecedor, "comprador": pega("comprador", texto, None),
            "qtd_cotacoes": int(cotacoes), "tipo_compra": tipo,
            "motivo_emergencia": pega("motivo_emergencia", texto, None) if tipo == "Emergencial" else None,
            "preco_unit_inicial": max(inicial, final), "preco_unit_final": final,
            "valor_itens": round(valor_itens, 2), "frete": frete, "custo_total": round(custo_total, 2),
            "prazo_pagamento_dias": int(pagamento),
            "custo_total_hoje": round(regras.custo_de_hoje(custo_total, pagamento), 2),
            "saving_negociacao": round((max(inicial, final) - final) * qtd, 2),
            "previsto_no_orcamento": orcamento,
            "prazo_entrega_dias": int(entrega) if entrega is not None else None,
            "origem": "planilha",
        })

    # Preço de referência de cada item = média das compras planejadas (planilha + base) -> pago a mais
    precos = {}
    for c in list(compras_existentes) + resultado["compras"]:
        if c["tipo_compra"] == "Planejada":
            precos.setdefault(c["codigo_item"], []).append(c["preco_unit_final"])
    for c in resultado["compras"]:
        lista = precos.get(c["codigo_item"])
        ref = round(sum(lista) / len(lista), 2) if lista else None
        c["preco_medio_planejado_item"] = ref
        c["pago_a_mais_vs_planejado"] = (round(max(c["preco_unit_final"] - ref, 0) * c["quantidade"], 2)
                                         if ref and c["tipo_compra"] == "Emergencial" else 0)
    sem_ref = sum(1 for c in resultado["compras"] if c["preco_medio_planejado_item"] is None)
    if sem_ref:
        resultado["avisos"].append(f"{sem_ref} compra(s) de itens sem nenhuma compra planejada: sem preço de referência, "
                                   "a nota não avalia o preço delas.")

    _preparar_cotacoes(abas.get("cotacoes"), resultado)
    return resultado


def _preparar_cotacoes(linhas_cot, resultado):
    """Monta as cotações: da aba 'cotacoes', se existir; senão, só a proposta vencedora de cada compra."""
    por_id = {c["id_compra"]: c for c in resultado["compras"]}
    if linhas_cot:
        cab, *linhas = linhas_cot
        mapa, _ = _mapear_cabecalho(cab, COLUNAS_COTACAO)
        faltando = [c for c, (obrig, _, _) in COLUNAS_COTACAO.items() if obrig and c not in mapa]
        if faltando:
            resultado["avisos"].append("A aba 'cotacoes' foi ignorada: faltam as colunas " +
                                       ", ".join(COLUNAS_COTACAO[c][2] for c in faltando) + ".")
            linhas = []
        for n_linha, linha in enumerate(linhas, start=2):
            v = lambda campo: linha[mapa[campo]] if campo in mapa and mapa[campo] < len(linha) else None
            compra = por_id.get(str(v("id_compra") or "").strip())
            if not compra:
                resultado["avisos"].append(f"Aba cotacoes, linha {n_linha}: pedido “{v('id_compra')}” não está na aba compras.")
                continue
            try:
                preco = _numero(v("preco_unit"))
                frete = _numero(v("frete")) or 0
                pagamento = int(_numero(v("prazo_pagamento_dias")) or 0)
                entrega = _numero(v("prazo_entrega_dias"))
            except ValueError:
                resultado["avisos"].append(f"Aba cotacoes, linha {n_linha}: valor não entendido, linha ignorada.")
                continue
            if not preco:
                continue
            vencedora = normalizar(v("fornecedor")) == normalizar(compra["fornecedor"])
            if vencedora:
                preco = compra["preco_unit_final"]
            resultado["cotacoes"].append(_cotacao(compra, str(v("fornecedor")).strip(), preco, frete, pagamento,
                                                  int(entrega) if entrega is not None else None, vencedora))
    com_cotacao = {c["id_compra"] for c in resultado["cotacoes"]}
    for compra in resultado["compras"]:
        if compra["id_compra"] not in com_cotacao or not any(
                c["vencedora"] == "Sim" for c in resultado["cotacoes"] if c["id_compra"] == compra["id_compra"]):
            resultado["cotacoes"].append(_cotacao(compra, compra["fornecedor"], compra["preco_unit_final"], compra["frete"],
                                                  compra["prazo_pagamento_dias"], compra["prazo_entrega_dias"], True))
    # marca a de melhor custo de hoje em cada pedido
    por_pedido = {}
    for c in resultado["cotacoes"]:
        por_pedido.setdefault(c["id_compra"], []).append(c)
    for lista in por_pedido.values():
        menor = min(c["custo_total_hoje"] for c in lista)
        for c in lista:
            c["melhor_custo_hoje"] = "Sim" if c["custo_total_hoje"] == menor else "Não"


def _cotacao(compra, fornecedor, preco, frete, pagamento, entrega, vencedora):
    total = preco * compra["quantidade"] + frete
    return {"id_compra": compra["id_compra"], "fornecedor": fornecedor, "preco_unit": preco,
            "quantidade": compra["quantidade"], "valor_itens": round(preco * compra["quantidade"], 2),
            "frete": frete, "custo_total": round(total, 2), "prazo_pagamento_dias": pagamento,
            "custo_total_hoje": round(regras.custo_de_hoje(total, pagamento), 2),
            "prazo_entrega_dias": entrega, "vencedora": "Sim" if vencedora else "Não"}


# ---------------------------------------------------------------------------
# Planilha modelo (para baixar e mostrar para a empresa)
# ---------------------------------------------------------------------------
def planilha_modelo():
    livro = Workbook()
    negrito, fundo = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="007936")

    def aba(planilha, colunas, exemplos):
        planilha.append([c for c in colunas])
        for celula in planilha[1]:
            celula.font, celula.fill = negrito, fundo
        for e in exemplos:
            planilha.append(e)
        for i, c in enumerate(colunas, start=1):
            planilha.column_dimensions[planilha.cell(1, i).column_letter].width = max(14, len(c) + 4)

    compras = livro.active
    compras.title = "compras"
    cols = list(COLUNAS_COMPRA)
    exemplo1 = {"data_pedido": "03/11/2026", "area_solicitante": "Produção (Abate e Cortes)", "item": "Luvas nitrílicas (cx c/ 100 un)",
                "quantidade": 150, "fornecedor": "ProtegeMais EPIs", "preco_unit_final": 36.90, "tipo_compra": "Planejada",
                "qtd_cotacoes": 3, "frete": 85, "prazo_pagamento_dias": 28, "prazo_entrega_dias": 14, "preco_unit_inicial": 37.64,
                "previsto_no_orcamento": "Sim", "categoria": "EPIs e Uniformes", "unidade": "cx", "id_compra": "OC-5001"}
    exemplo2 = {"data_pedido": "04/11/2026", "area_solicitante": "Manutenção Industrial", "item": "Rolamento 6205 (un)",
                "quantidade": 20, "fornecedor": "Casa das Peças Express", "preco_unit_final": 54.00, "tipo_compra": "Emergencial",
                "qtd_cotacoes": 1, "frete": 0, "prazo_pagamento_dias": 0, "prazo_entrega_dias": 0,
                "previsto_no_orcamento": "Não", "categoria": "Peças de Manutenção", "unidade": "un", "id_compra": "OC-5002",
                "motivo_emergencia": "Quebra na linha 2"}
    aba(compras, cols, [[e.get(c, "") for c in cols] for e in (exemplo1, exemplo2)])

    cotacoes = livro.create_sheet("cotacoes")
    ccols = list(COLUNAS_COTACAO)
    aba(cotacoes, ccols, [["OC-5001", "ProtegeMais EPIs", 37.64, 85, 28, 14],
                          ["OC-5001", "EPI Center Recife", 38.90, 0, 30, 12],
                          ["OC-5001", "EPI Já Distribuidora", 46.20, 120, 0, 1]])

    instrucoes = livro.create_sheet("instrucoes")
    instrucoes.append(["Coluna", "Obrigatória?", "O que colocar", "Outros nomes aceitos"])
    for celula in instrucoes[1]:
        celula.font, celula.fill = negrito, fundo
    for titulo, colunas in (("ABA compras", COLUNAS_COMPRA), ("ABA cotacoes (opcional)", COLUNAS_COTACAO)):
        instrucoes.append([titulo])
        instrucoes.cell(instrucoes.max_row, 1).font = Font(bold=True)
        for campo, (obrig, apelidos, explicacao) in colunas.items():
            instrucoes.append([campo, "Sim" if obrig else "Não", explicacao, ", ".join(apelidos)])
    for letra, largura in zip("ABCD", (24, 14, 60, 70)):
        instrucoes.column_dimensions[letra].width = largura

    arquivo = io.BytesIO()
    livro.save(arquivo)
    return arquivo.getvalue()
