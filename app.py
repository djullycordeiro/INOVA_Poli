"""
app.py — o site. Cada função com @app.route é uma página.

Para rodar:
    python app.py      -> abre em http://localhost:5000
"""

import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import banco
import importacao
import regras
from seguranca import (configurar, filtro_area, limpar_erros_login, login_bloqueado,
                       login_obrigatorio, novo_codigo_convite, registrar_erro_login,
                       so_gerente, usuario_atual)

app = Flask(__name__)
configurar(app)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024   # planilhas de até 10 MB
PASTA_UPLOADS = Path(__file__).parent / "uploads"   # está no .gitignore: dados reais nunca vão pro GitHub
banco.garantir_tabelas()

VALIDADE_CONVITE_DIAS = 7

# As 6 telas da barra lateral: (endereço, nome no menu)
TELAS = [
    ("cotacoes", "Cotações"),
    ("nota", "Nota da Compra"),
    ("historico", "Histórico de preços"),
    ("savings", "Savings"),
    ("radar", "Radar de Emergenciais"),
    ("planos", "Planos"),
]


@app.before_request
def atualizar_periodo():
    """Antes de cada página: descobre a compra mais recente e define o período móvel de 12 meses."""
    with banco.conectar() as c:
        ultima, do_site = c.execute(
            "SELECT MAX(data_pedido), SUM(origem = 'site') FROM compras").fetchone()
    if ultima:
        regras.definir_periodo(date.fromisoformat(ultima[:10]), bool(do_site))
    else:   # base vazia: período = os 12 meses até hoje
        regras.definir_periodo(date.today(), True)
    g.base = banco.base_ativa()


def no_periodo():
    """Pedaço de SQL que limita as compras ao período móvel."""
    return "data_pedido BETWEEN ? AND ?", [regras.INICIO_PERIODO.isoformat(), regras.FIM_PERIODO.isoformat()]


def filtro_compras(u):
    """Compras que o usuário pode ver: a área dele (gerente vê todas) E dentro do período."""
    onde_area, p_area = filtro_area(u)
    onde_per, p_per = no_periodo()
    return f"({onde_area}) AND {onde_per}", p_area + p_per


def todas_no_periodo(c):
    """A empresa inteira, dentro do período (para medianas e comparações)."""
    onde, params = no_periodo()
    return c.execute(f"SELECT * FROM compras WHERE {onde}", params).fetchall()


def radar_do_usuario(u):
    """Calcula o Radar uma vez por página (fica guardado em g durante a requisição)."""
    if "radar" not in g:
        onde, params = filtro_compras(u)
        with banco.conectar() as c:
            compras = c.execute(f"SELECT * FROM compras WHERE {onde}", params).fetchall()
            todas = todas_no_periodo(c)
        g.radar = regras.radar(compras, todas, u["area"] if u["perfil"] == "analista" else None)
    return g.radar


@app.context_processor
def variaveis_globais():
    """Disponível em todos os templates."""
    u = usuario_atual()
    alertas = len(radar_do_usuario(u)["alertas"]) if u else 0
    abertas = 0
    if u and u["perfil"] == "gerente":
        with banco.conectar() as c:
            abertas = c.execute("SELECT COUNT(*) FROM requisicoes WHERE status = 'aberta'").fetchone()[0]
    return {"usuario": u, "telas": TELAS, "csrf": session.get("csrf", ""), "n_alertas": alertas, "n_abertas": abertas,
            "periodo": regras.periodo_texto(), "base": banco.BASES[g.get("base", banco.BASE_PADRAO)],
            "base_chave": g.get("base", banco.BASE_PADRAO)}


# ---------------------------------------------------------------------------
# Login, cadastro e saída
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        if login_bloqueado(email):
            flash("Muitas tentativas. Aguarde 15 minutos e tente de novo.")
            return render_template("login.html"), 429
        with banco.conectar() as c:
            u = c.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
        if u and check_password_hash(u["senha_hash"], senha):
            session.clear()
            session["usuario_id"] = u["id"]
            session.permanent = True
            limpar_erros_login(email)
            return redirect(url_for("cotacoes"))
        registrar_erro_login(email)
        # Mesma mensagem para e-mail inexistente e senha errada: não revela quem tem conta.
        flash("E-mail ou senha incorretos.")
    return render_template("login.html")


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip().upper()
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        with banco.conectar() as c:
            convite = c.execute(
                "SELECT * FROM convites WHERE codigo = ? AND usado_por IS NULL AND cancelado = 0"
                " AND expira_em > datetime('now')", (codigo,)).fetchone()
            ja_existe = c.execute("SELECT 1 FROM usuarios WHERE email = ?", (email,)).fetchone()
            if not convite:
                flash("Código de convite inválido, expirado ou já usado.")
            elif not nome or "@" not in email:
                flash("Preencha nome e e-mail.")
            elif len(senha) < 8:
                flash("A senha precisa ter pelo menos 8 caracteres.")
            elif senha != request.form.get("senha2", ""):
                flash("As senhas não conferem.")
            elif ja_existe:
                flash("Já existe uma conta com esse e-mail.")
            else:
                cur = c.execute(
                    "INSERT INTO usuarios (nome, email, senha_hash, perfil, area) VALUES (?, ?, ?, ?, ?)",
                    (nome, email, generate_password_hash(senha), convite["perfil"], convite["area"]))
                c.execute("UPDATE convites SET usado_por = ?, usado_em = datetime('now') WHERE codigo = ?",
                          (cur.lastrowid, codigo))
                flash("Conta criada! Agora é só entrar.")
                return redirect(url_for("login"))
    return render_template("cadastro.html")


@app.route("/sair", methods=["POST"])
def sair():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Convites (só gerente)
# ---------------------------------------------------------------------------
@app.route("/convites", methods=["GET", "POST"])
@so_gerente
def convites():
    u = usuario_atual()
    if request.method == "POST":
        acao = request.form.get("acao")
        with banco.conectar() as c:
            if acao == "gerar":
                perfil = request.form.get("perfil")
                area = request.form.get("area") if perfil == "analista" else None
                if perfil not in ("analista", "gerente") or (perfil == "analista" and area not in banco.areas()):
                    flash("Escolha o perfil e a área.")
                else:
                    codigo = novo_codigo_convite()
                    expira = (datetime.now(timezone.utc) + timedelta(days=VALIDADE_CONVITE_DIAS)).strftime("%Y-%m-%d %H:%M:%S")
                    c.execute("INSERT INTO convites (codigo, perfil, area, criado_por, expira_em) VALUES (?, ?, ?, ?, ?)",
                              (codigo, perfil, area, u["id"], expira))
                    flash(f"Convite gerado: {codigo}")
            elif acao == "cancelar":
                c.execute("UPDATE convites SET cancelado = 1 WHERE codigo = ? AND usado_por IS NULL",
                          (request.form.get("codigo"),))
        return redirect(url_for("convites"))

    with banco.conectar() as c:
        lista = c.execute("""
            SELECT cv.*, criador.nome AS criador_nome, usuario.nome AS usado_nome,
                   CASE WHEN cv.cancelado = 1 THEN 'Cancelado'
                        WHEN cv.usado_por IS NOT NULL THEN 'Usado'
                        WHEN cv.expira_em <= datetime('now') THEN 'Expirado'
                        ELSE 'Pendente' END AS situacao
            FROM convites cv
            JOIN usuarios criador ON criador.id = cv.criado_por
            LEFT JOIN usuarios usuario ON usuario.id = cv.usado_por
            ORDER BY cv.criado_em DESC""").fetchall()
    return render_template("convites.html", convites=lista, areas=banco.areas(), validade=VALIDADE_CONVITE_DIAS)



# ---------------------------------------------------------------------------
# 3.1 Cotações
# ---------------------------------------------------------------------------
FILTROS_COTACAO = [
    ("todos", "Todos"),
    ("caro", "Escolheu mais caro"),
    ("emergencial", "Emergenciais"),
    ("unica", "1 só cotação"),
]


@app.route("/cotacoes")
@login_obrigatorio
def cotacoes():
    u = usuario_atual()
    onde, params = filtro_compras(u)
    filtro = request.args.get("filtro", "todos")

    with banco.conectar() as c:
        compras = c.execute(f"SELECT * FROM compras WHERE {onde} ORDER BY data_pedido DESC", params).fetchall()
        todas_propostas = c.execute(
            f"SELECT * FROM cotacoes WHERE id_compra IN (SELECT id_compra FROM compras WHERE {onde})"
            " ORDER BY custo_total_hoje", params).fetchall()

    # Agrupa as propostas por pedido e analisa cada um
    por_pedido = {}
    for p in todas_propostas:
        por_pedido.setdefault(p["id_compra"], []).append(p)
    pedidos = []
    for compra in compras:
        analise = regras.analisar_cotacao(compra, por_pedido.get(compra["id_compra"], []))
        pedidos.append({"compra": compra, "analise": analise})

    contagem = {
        "todos": len(pedidos),
        "caro": sum(p["analise"]["veredito"] in ("cara", "pressa") for p in pedidos),
        "emergencial": sum(p["compra"]["tipo_compra"] == "Emergencial" for p in pedidos),
        "unica": sum(p["analise"]["veredito"] == "unica" for p in pedidos),
    }
    visiveis = {
        "todos": pedidos,
        "caro": [p for p in pedidos if p["analise"]["veredito"] in ("cara", "pressa")],
        "emergencial": [p for p in pedidos if p["compra"]["tipo_compra"] == "Emergencial"],
        "unica": [p for p in pedidos if p["analise"]["veredito"] == "unica"],
    }.get(filtro, pedidos)

    # Pedido aberto: o da URL (?sel=PC-1004) ou o primeiro da lista
    sel_id = request.args.get("sel")
    selecionado = next((p for p in pedidos if p["compra"]["id_compra"] == sel_id), None)
    if selecionado is None and visiveis:
        selecionado = visiveis[0]

    return render_template("cotacoes.html", pedidos=visiveis, sel=selecionado, filtro=filtro,
                           filtros=FILTROS_COTACAO, contagem=contagem, lida=request.args.get("lida") == "1",
                           r=regras)


# ---------------------------------------------------------------------------
# 3.2 Nota da Compra
# ---------------------------------------------------------------------------
FILTROS_NOTA = [("todas", "Todas"), ("r", "Vermelhas"), ("a", "Amarelas"), ("v", "Verdes")]


def compras_com_nota(usuario):
    """Busca as compras da área do usuário e calcula a nota de cada uma.
    Usado pela tela de Nota e, depois, pelo Radar."""
    onde, params = filtro_compras(usuario)
    with banco.conectar() as c:
        compras = c.execute(f"SELECT * FROM compras WHERE {onde} ORDER BY data_pedido DESC", params).fetchall()
        propostas = c.execute(
            f"SELECT * FROM cotacoes WHERE id_compra IN (SELECT id_compra FROM compras WHERE {onde})", params).fetchall()
        # "Consumo previsível" olha a empresa toda, não só a área
        p_onde, p_params = no_periodo()
        vezes = dict(c.execute(f"SELECT codigo_item, COUNT(*) FROM compras WHERE {p_onde} GROUP BY codigo_item",
                               p_params).fetchall())
    por_pedido = {}
    for p in propostas:
        por_pedido.setdefault(p["id_compra"], []).append(p)
    resultado = []
    for compra in compras:
        analise = regras.analisar_cotacao(compra, por_pedido.get(compra["id_compra"], []))
        nota = regras.nota_da_compra(compra, analise, vezes.get(compra["codigo_item"], 0))
        resultado.append({"compra": compra, "analise": analise, **nota})
    return resultado


@app.route("/nota")
@login_obrigatorio
def nota():
    todas = compras_com_nota(usuario_atual())
    filtro = request.args.get("filtro", "todas")
    visiveis = todas if filtro == "todas" else [x for x in todas if x["cor"] == filtro]

    total = len(todas) or 1
    resumo = []
    for cor in ("v", "a", "r"):
        n = sum(x["cor"] == cor for x in todas)
        pct = f"{n / total * 100:.1f}".replace(".", ",").replace(",0", "")
        resumo.append({"cor": cor, "n": n, "pct": pct, **regras.CORES_NOTA[cor]})
    contagem = {"todas": len(todas), **{r["cor"]: r["n"] for r in resumo}}

    sel_id = request.args.get("sel")
    sel = next((x for x in todas if x["compra"]["id_compra"] == sel_id), None)
    if sel is None:
        # Sem compra escolhida: abre na vermelha mais recente da lista (a lista já vem da mais nova para a mais antiga)
        sel = next((x for x in visiveis if x["cor"] == "r"), visiveis[0] if visiveis else None)
    return render_template("nota.html", lista=visiveis, sel=sel, resumo=resumo, filtro=filtro,
                           filtros=FILTROS_NOTA, contagem=contagem, cores=regras.CORES_NOTA, r=regras)


# ---------------------------------------------------------------------------
# 3.3 Histórico de preços
# ---------------------------------------------------------------------------
@app.route("/historico")
@login_obrigatorio
def historico():
    onde, params = filtro_compras(usuario_atual())
    with banco.conectar() as c:
        compras = c.execute(f"SELECT * FROM compras WHERE {onde}", params).fetchall()

    # Agrupa por item
    por_item = {}
    for cp in compras:
        por_item.setdefault(cp["codigo_item"], []).append(cp)

    # Dropdown: todos os itens, agrupados por categoria
    categorias = {}
    for cod, lista in sorted(por_item.items(), key=lambda kv: kv[1][0]["item"]):
        categorias.setdefault(lista[0]["categoria"], []).append((cod, lista[0]["item"], len(lista)))

    # Ranking: os 5 itens com mais compras emergenciais (desempate: quanto pagou a mais)
    def peso(lista):
        emerg = [x for x in lista if x["tipo_compra"] == "Emergencial"]
        return (len(emerg), sum(x["pago_a_mais_vs_planejado"] or 0 for x in emerg))
    mais_emergenciais = [
        {"codigo": cod, "nome": regras.nome_curto(lista[0]["item"]), "n": peso(lista)[0], "extra": peso(lista)[1]}
        for cod, lista in sorted(por_item.items(), key=lambda kv: peso(kv[1]), reverse=True)[:5]
        if peso(lista)[0] > 0]

    codigo = request.args.get("item")
    if codigo not in por_item:
        codigo = mais_emergenciais[0]["codigo"] if mais_emergenciais else next(iter(por_item), None)
    hist = regras.historico_do_item(por_item[codigo]) if codigo else None

    return render_template("historico.html", hist=hist, codigo=codigo, categorias=sorted(categorias.items()),
                           mais_emergenciais=mais_emergenciais, r=regras)


# ---------------------------------------------------------------------------
# 3.4 Savings
# ---------------------------------------------------------------------------
@app.route("/savings")
@login_obrigatorio
def savings():
    onde, params = filtro_compras(usuario_atual())
    with banco.conectar() as c:
        compras = c.execute(f"SELECT * FROM compras WHERE {onde}", params).fetchall()
        todas = todas_no_periodo(c)
    # O desconto por cotações usa a empresa toda: numa área só, a amostra é pequena demais
    return render_template("savings.html", s=regras.savings(compras),
                           por_cotacoes=regras.desconto_por_cotacoes(todas), r=regras)


# ---------------------------------------------------------------------------
# 3.5 Radar de Emergenciais
# ---------------------------------------------------------------------------
@app.route("/radar", methods=["GET", "POST"])
@login_obrigatorio
def radar():
    u = usuario_atual()
    dados = radar_do_usuario(u)
    cartoes = {c["chave"]: c for c in dados["prevencao"]}

    if request.method == "POST":
        cartao = cartoes.get(request.form.get("chave"))
        if cartao is None:  # só aceita cartões que o próprio usuário está vendo
            abort(400)
        with banco.conectar() as c:
            ja = c.execute("SELECT 1 FROM requisicoes WHERE chave = ?", (cartao["chave"],)).fetchone()
            if not ja:
                c.execute("INSERT INTO requisicoes (chave, tipo, titulo, itens, area, data_limite, criado_por)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (cartao["chave"], cartao["tipo"], cartao["titulo"], cartao["itens"], cartao["area"],
                           cartao["data_limite"], u["id"]))
                flash("Requisição criada e enviada para aprovação em Requisições." if cartao["tipo"] == "planejada" else "Estoque mínimo registrado em Requisições.")
        return redirect(url_for("radar") + "#antes")

    with banco.conectar() as c:
        feitas = {r["chave"]: r for r in c.execute(
            "SELECT r.*, u.nome AS autor FROM requisicoes r JOIN usuarios u ON u.id = r.criado_por").fetchall()}
    return render_template("radar.html", rd=dados, feitas=feitas, r=regras)


# ---------------------------------------------------------------------------
# 3.6 Planos
# ---------------------------------------------------------------------------
@app.route("/planos")
@login_obrigatorio
def planos():
    def opcao(nome, opcoes, padrao):
        valor = request.args.get(nome, type=int)
        return valor if valor in opcoes else padrao
    gasto = opcao("gasto", regras.OPCOES_GASTO, 10)
    eco = opcao("eco", regras.OPCOES_ECONOMIA, 2)
    return render_template("planos.html", gasto=gasto, eco=eco, sim=regras.simular_plano(gasto, eco), r=regras)


# ---------------------------------------------------------------------------
# 4.2 Requisições
# ---------------------------------------------------------------------------
STATUS_REQUISICAO = [("todas", "Todas"), ("aberta", "Abertas"), ("aprovada", "Aprovadas"),
                     ("recusada", "Recusadas"), ("concluida", "Concluídas")]


def catalogo_de_itens():
    """Itens que já existem na base, com o prazo médio de entrega das compras planejadas."""
    with banco.conectar() as c:
        return c.execute("""
            SELECT codigo_item, item, unidade, categoria,
                   ROUND(AVG(CASE WHEN tipo_compra = 'Planejada' THEN prazo_entrega_dias END)) AS prazo_medio
            FROM compras GROUP BY codigo_item ORDER BY categoria, item""").fetchall()


@app.route("/requisicoes", methods=["GET", "POST"])
@login_obrigatorio
def requisicoes():
    u = usuario_atual()
    itens = {i["codigo_item"]: i for i in catalogo_de_itens()}
    hoje = regras.DATA_DE_HOJE
    form, aviso = {}, None

    if request.method == "POST":
        form = request.form
        item = itens.get(form.get("item"))
        area = form.get("area") if u["perfil"] == "gerente" else u["area"]
        try:
            quantidade = float(form.get("quantidade", "").replace(",", "."))
            data_nec = date.fromisoformat(form.get("data_necessaria", ""))
        except ValueError:
            quantidade, data_nec = 0, None
        urgencia = form.get("urgencia") if form.get("urgencia") in ("Planejada", "Emergencial") else "Planejada"

        if not item or area not in banco.areas():
            flash("Escolha o item e a área.")
        elif quantidade <= 0:
            flash("A quantidade precisa ser maior que zero.")
        elif data_nec is None or data_nec < hoje:
            flash(f"A data precisa ser a partir de {regras.data_br(hoje.isoformat())}.")
        else:
            dias = (data_nec - hoje).days
            prazo = int(item["prazo_medio"] or 0)
            if urgencia == "Planejada" and prazo and dias < prazo and not form.get("confirmado"):
                # Avisa antes de salvar: com esse prazo, não dá tempo de comprar planejado
                aviso = {"item": regras.nome_curto(item["item"]), "prazo": prazo, "dias": dias}
            else:
                if urgencia == "Planejada" and prazo and dias < prazo:
                    urgencia = "Emergencial"
                with banco.conectar() as c:
                    c.execute("""INSERT INTO requisicoes (chave, tipo, titulo, itens, area, quantidade, data_necessaria,
                                 urgencia, justificativa, criado_por) VALUES (?, 'planejada', ?, ?, ?, ?, ?, ?, ?, ?)""",
                              (f"manual:{secrets.token_hex(6)}", item["item"], item["codigo_item"], area, quantidade,
                               data_nec.isoformat(), urgencia, form.get("justificativa", "").strip(), u["id"]))
                flash("Requisição criada. Agora ela aguarda a aprovação do gerente." if u["perfil"] != "gerente"
                      else "Requisição criada.")
                return redirect(url_for("requisicoes"))

    filtro = request.args.get("status", "todas")
    onde, params = ("1 = 1", []) if u["perfil"] == "gerente" else ("r.area = ?", [u["area"]])
    with banco.conectar() as c:
        lista = c.execute(f"""
            SELECT r.*, autor.nome AS autor, quem.nome AS decidiu,
                   (SELECT COUNT(*) FROM propostas p WHERE p.requisicao_id = r.id) AS n_propostas
            FROM requisicoes r JOIN usuarios autor ON autor.id = r.criado_por
            LEFT JOIN usuarios quem ON quem.id = r.decidido_por
            WHERE {onde} ORDER BY r.status != 'aberta', r.criado_em DESC""", params).fetchall()
    contagem = {k: (len(lista) if k == "todas" else sum(x["status"] == k for x in lista)) for k, _ in STATUS_REQUISICAO}
    visiveis = lista if filtro == "todas" else [x for x in lista if x["status"] == filtro]
    return render_template("requisicoes.html", lista=visiveis, filtro=filtro, filtros=STATUS_REQUISICAO,
                           contagem=contagem, itens=itens.values(), unidades={k: i["unidade"] for k, i in itens.items()},
                           areas=banco.areas(), form=form, aviso=aviso,
                           hoje=hoje, r=regras)


@app.route("/requisicoes/<int:id>/decidir", methods=["POST"])
@so_gerente
def decidir_requisicao(id):
    u = usuario_atual()
    acao = request.form.get("acao")
    motivo = request.form.get("motivo", "").strip()
    if acao == "recusar" and not motivo:
        flash("Para recusar, escreva o motivo.")
    elif acao in ("aprovar", "recusar"):
        with banco.conectar() as c:
            c.execute("""UPDATE requisicoes SET status = ?, decidido_por = ?, decidido_em = datetime('now'),
                         motivo_recusa = ? WHERE id = ? AND status = 'aberta'""",
                      ("aprovada" if acao == "aprovar" else "recusada", u["id"], motivo or None, id))
        flash("Requisição aprovada." if acao == "aprovar" else "Requisição recusada.")
    return redirect(url_for("requisicoes", status=request.form.get("voltar", "todas")))


# ---------------------------------------------------------------------------
# 4.3 Lançar cotações de uma requisição aprovada
# ---------------------------------------------------------------------------
def requisicao_permitida(id, u):
    """Busca a requisição e confere se o usuário pode mexer nela (gerente ou analista da área)."""
    with banco.conectar() as c:
        req = c.execute("SELECT * FROM requisicoes WHERE id = ?", (id,)).fetchone()
    if req is None:
        abort(404)
    if u["perfil"] != "gerente" and req["area"] != u["area"]:
        abort(403)
    return req


def _numero_do_form(nome, inteiro=False):
    """'1.234,56' ou '1234.56' -> 1234.56; vazio -> 0; inválido -> None"""
    texto = request.form.get(nome, "").strip()
    if not texto:
        return 0
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        valor = float(texto)
    except ValueError:
        return None
    if valor < 0:
        return None
    return int(valor) if inteiro else valor


@app.route("/requisicoes/<int:id>/cotacoes", methods=["GET", "POST"])
@login_obrigatorio
def cotar_requisicao(id):
    u = usuario_atual()
    req = requisicao_permitida(id, u)
    if req["tipo"] != "planejada" or req["status"] not in ("aprovada", "concluida"):
        flash("Só dá para lançar cotações de requisições de compra já aprovadas.")
        return redirect(url_for("requisicoes"))
    pode_editar = req["status"] == "aprovada"

    if request.method == "POST" and pode_editar:
        acao = request.form.get("acao")
        with banco.conectar() as c:
            if acao == "quantidade":
                qtd = _numero_do_form("quantidade")
                if not qtd:
                    flash("Informe uma quantidade maior que zero.")
                else:
                    c.execute("UPDATE requisicoes SET quantidade = ? WHERE id = ?", (qtd, id))
            elif acao == "adicionar":
                fornecedor = request.form.get("fornecedor", "").strip()
                preco = _numero_do_form("preco_unit")
                frete = _numero_do_form("frete")
                pagamento = _numero_do_form("prazo_pagamento_dias", inteiro=True)
                entrega = _numero_do_form("prazo_entrega_dias", inteiro=True)
                if not fornecedor:
                    flash("Informe o fornecedor.")
                elif not preco:
                    flash("Informe o preço unitário.")
                elif None in (frete, pagamento, entrega):
                    flash("Frete e prazos precisam ser números (use 0 para à vista ou frete grátis).")
                elif c.execute("SELECT 1 FROM propostas WHERE requisicao_id = ? AND fornecedor = ?", (id, fornecedor)).fetchone():
                    flash(f"Já existe uma proposta de {fornecedor} nesta requisição. Exclua a antiga para lançar de novo.")
                else:
                    c.execute("""INSERT INTO propostas (requisicao_id, fornecedor, preco_unit, frete, prazo_pagamento_dias,
                                 prazo_entrega_dias, criado_por) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                              (id, fornecedor, preco, frete, pagamento, entrega, u["id"]))
                    flash(f"Proposta de {fornecedor} lançada.")
            elif acao == "excluir":
                c.execute("DELETE FROM propostas WHERE id = ? AND requisicao_id = ?", (request.form.get("proposta"), id))
                flash("Proposta excluída.")
        return redirect(url_for("cotar_requisicao", id=id))

    with banco.conectar() as c:
        linhas = c.execute("SELECT * FROM propostas WHERE requisicao_id = ? ORDER BY criado_em", (id,)).fetchall()
        fornecedores = [l[0] for l in c.execute(
            "SELECT DISTINCT fornecedor FROM cotacoes UNION SELECT DISTINCT fornecedor FROM propostas ORDER BY 1")]
        item = c.execute("SELECT item, unidade, categoria FROM compras WHERE codigo_item = ? LIMIT 1",
                         (req["itens"],)).fetchone()
    descontos = {x["k"]: x["pct"] for x in regras.desconto_por_cotacoes(banco_todas_compras())}
    analise = regras.comparar_propostas(linhas, req["quantidade"] or 0, req["data_necessaria"], regras.DATA_DE_HOJE)
    return render_template("cotar.html", req=req, item=item, analise=analise, fornecedores=fornecedores,
                           descontos=descontos, pode_editar=pode_editar, r=regras)


@app.route("/requisicoes/<int:id>/registrar", methods=["POST"])
@login_obrigatorio
def registrar_compra(id):
    """4.4 — Fecha a requisição: escolhe a proposta vencedora e cria a compra."""
    u = usuario_atual()
    req = requisicao_permitida(id, u)
    if req["status"] != "aprovada" or not req["quantidade"]:
        flash("Esta requisição não pode mais ser registrada.")
        return redirect(url_for("cotar_requisicao", id=id))
    with banco.conectar() as c:
        propostas = c.execute("SELECT * FROM propostas WHERE requisicao_id = ?", (id,)).fetchall()
        item = c.execute("SELECT codigo_item, item, unidade, categoria FROM compras WHERE codigo_item = ? LIMIT 1",
                         (req["itens"],)).fetchone()
        planejados = [l[0] for l in c.execute(
            "SELECT preco_unit_final FROM compras WHERE codigo_item = ? AND tipo_compra = 'Planejada'", (req["itens"],))]
        ultimo = c.execute("SELECT MAX(CAST(SUBSTR(id_compra, 4) AS INTEGER)) FROM compras").fetchone()[0] or 1000

    vencedora = request.form.get("vencedora", type=int)
    preco_final = _numero_do_form("preco_final")
    venc = next((p for p in propostas if p["id"] == vencedora), None)
    if venc is None:
        flash("Escolha a proposta vencedora.")
    elif not preco_final:
        flash("Informe o preço final negociado.")
    elif preco_final > venc["preco_unit"]:
        flash("O preço final não pode ser maior que o da proposta. Se o fornecedor aumentou, lance uma nova proposta.")
    else:
        id_compra = f"PC-{ultimo + 1}"
        compra, cotacoes = regras.montar_compra(
            id_compra, req, item, propostas, vencedora, preco_final, request.form.get("orcamento") == "sim",
            u["nome"], planejados, date.today())
        with banco.conectar() as c:
            c.execute(f"INSERT INTO compras ({', '.join(compra)}) VALUES ({', '.join('?' * len(compra))})",
                      list(compra.values()))
            for q in cotacoes:
                c.execute(f"INSERT INTO cotacoes ({', '.join(q)}) VALUES ({', '.join('?' * len(q))})", list(q.values()))
            c.execute("UPDATE requisicoes SET status = 'concluida', id_compra = ? WHERE id = ?", (id_compra, id))
        flash(f"Compra {id_compra} registrada. Veja a nota dela abaixo.")
        return redirect(url_for("nota", sel=id_compra))
    return redirect(url_for("cotar_requisicao", id=id) + "#registrar")


def banco_todas_compras():
    with banco.conectar() as c:
        return todas_no_periodo(c)


# ---------------------------------------------------------------------------
# 4.5 Bases de dados (Pitch x Natto) — só gerente
# ---------------------------------------------------------------------------
@app.route("/bases", methods=["GET", "POST"])
@so_gerente
def bases():
    if request.method == "POST":
        escolhida = request.form.get("base")
        if escolhida in banco.BASES:
            banco.definir_base(escolhida)
            flash(f"Agora o site está usando a base {banco.BASES[escolhida]['nome']} ({banco.BASES[escolhida]['descricao']}) "
                  "para todos os usuários.")
        return redirect(url_for("bases"))
    resumo = []
    for chave, info in banco.BASES.items():
        with banco.conectar(chave) as c:
            n = c.execute("SELECT COUNT(*) FROM compras").fetchone()[0]
            r = c.execute("SELECT COUNT(*) FROM requisicoes").fetchone()[0]
            ultima = c.execute("SELECT MAX(data_pedido) FROM compras").fetchone()[0]
        resumo.append({"chave": chave, **info, "compras": n, "requisicoes": r, "ultima": ultima})
    return render_template("bases.html", bases=resumo, r=regras)


# ---------------------------------------------------------------------------
# 4.6 Importar compras por planilha — só gerente
# ---------------------------------------------------------------------------
def _arquivo_previa():
    token = session.get("importacao")
    if not token or not re.fullmatch(r"[0-9a-f]{32}", token):
        return None
    return PASTA_UPLOADS / f"importacao_{token}.json"


@app.route("/importar", methods=["GET", "POST"])
@so_gerente
def importar():
    if request.method == "POST":
        arquivo = request.files.get("planilha")
        if not arquivo or not arquivo.filename:
            flash("Escolha uma planilha .xlsx ou .csv.")
            return redirect(url_for("importar"))
        try:
            abas = importacao.ler_planilha(arquivo.read(), arquivo.filename)
        except ValueError as erro:
            flash(str(erro))
            return redirect(url_for("importar"))
        except Exception:
            flash("Não consegui abrir esse arquivo. Confira se é uma planilha .xlsx ou .csv válida.")
            return redirect(url_for("importar"))
        with banco.conectar() as c:
            existentes = c.execute("SELECT * FROM compras").fetchall()
            ultimo = c.execute("SELECT MAX(CAST(SUBSTR(id_compra, 4) AS INTEGER)) FROM compras "
                               "WHERE id_compra LIKE 'PC-%'").fetchone()[0] or 1000
        previa = importacao.preparar(abas, existentes, ultimo)
        previa["arquivo"] = arquivo.filename
        PASTA_UPLOADS.mkdir(exist_ok=True)
        antigo = _arquivo_previa()
        if antigo and antigo.exists():
            antigo.unlink()
        session["importacao"] = secrets.token_hex(16)
        _arquivo_previa().write_text(json.dumps(previa, ensure_ascii=False))
        return redirect(url_for("importar"))

    caminho = _arquivo_previa()
    previa = json.loads(caminho.read_text()) if caminho and caminho.exists() else None
    with banco.conectar() as c:
        n_base = c.execute("SELECT COUNT(*) FROM compras").fetchone()[0]
    return render_template("importar.html", previa=previa, n_base=n_base, colunas=importacao.COLUNAS_COMPRA, r=regras)


@app.route("/importar/confirmar", methods=["POST"])
@so_gerente
def confirmar_importacao():
    caminho = _arquivo_previa()
    if not caminho or not caminho.exists():
        flash("A prévia expirou. Envie a planilha de novo.")
        return redirect(url_for("importar"))
    previa = json.loads(caminho.read_text())
    if request.form.get("acao") == "cancelar" or not previa["compras"]:
        caminho.unlink()
        session.pop("importacao", None)
        flash("Importação cancelada. Nada foi gravado.")
        return redirect(url_for("importar"))
    substituir = request.form.get("modo") == "substituir"
    if substituir and g.base != "natto":
        flash("Substituir tudo só é permitido na base Natto. Para a base Pitch, use 'python banco.py limpar-demo'.")
        return redirect(url_for("importar"))
    with banco.conectar() as c:
        if substituir:
            banco.apagar_movimento(c, so_do_site=False)
        for compra in previa["compras"]:
            c.execute(f"INSERT INTO compras ({', '.join(compra)}) VALUES ({', '.join('?' * len(compra))})", list(compra.values()))
        for cot in previa["cotacoes"]:
            c.execute(f"INSERT INTO cotacoes ({', '.join(cot)}) VALUES ({', '.join('?' * len(cot))})", list(cot.values()))
    caminho.unlink()
    session.pop("importacao", None)
    flash(f"{len(previa['compras'])} compra(s) importada(s) na base {banco.BASES[g.base]['nome']}."
          + (" As compras anteriores desta base foram substituídas." if substituir else ""))
    return redirect(url_for("nota"))


@app.route("/importar/modelo.xlsx")
@login_obrigatorio
def modelo_importacao():
    return Response(importacao.planilha_modelo(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=justplan_modelo_importacao.xlsx"})


# ---------------------------------------------------------------------------
# 4.1 Perfil
# ---------------------------------------------------------------------------
@app.route("/perfil", methods=["GET", "POST"])
@login_obrigatorio
def perfil():
    u = usuario_atual()
    if request.method == "POST":
        acao = request.form.get("acao")
        with banco.conectar() as c:
            if acao == "nome":
                nome = request.form.get("nome", "").strip()
                if not nome:
                    flash("O nome não pode ficar vazio.")
                else:
                    c.execute("UPDATE usuarios SET nome = ? WHERE id = ?", (nome, u["id"]))
                    flash("Nome atualizado.")
            elif acao == "senha":
                atual = request.form.get("atual", "")
                nova = request.form.get("nova", "")
                if not check_password_hash(u["senha_hash"], atual):
                    flash("A senha atual está incorreta.")
                elif len(nova) < 8:
                    flash("A nova senha precisa ter pelo menos 8 caracteres.")
                elif nova != request.form.get("nova2", ""):
                    flash("As senhas novas não conferem.")
                elif nova == atual:
                    flash("A nova senha precisa ser diferente da atual.")
                else:
                    c.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (generate_password_hash(nova), u["id"]))
                    flash("Senha alterada.")
        return redirect(url_for("perfil"))
    return render_template("perfil.html")


@app.route("/")
def inicio():
    return redirect(url_for("cotacoes"))


if __name__ == "__main__":
    app.run(debug=True)
