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
