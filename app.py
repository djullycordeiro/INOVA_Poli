"""
app.py — o site. Cada função com @app.route é uma página.

Para rodar:
    python app.py      -> abre em http://localhost:5000
"""

from datetime import datetime, timedelta, timezone

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import banco
import regras
from seguranca import (configurar, filtro_area, limpar_erros_login, login_bloqueado,
                       login_obrigatorio, novo_codigo_convite, registrar_erro_login,
                       so_gerente, usuario_atual)

app = Flask(__name__)
configurar(app)
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


def radar_do_usuario(u):
    """Calcula o Radar uma vez por página (fica guardado em g durante a requisição)."""
    if "radar" not in g:
        onde, params = filtro_area(u)
        with banco.conectar() as c:
            compras = c.execute(f"SELECT * FROM compras WHERE {onde}", params).fetchall()
            todas = c.execute("SELECT * FROM compras").fetchall()
        g.radar = regras.radar(compras, todas, u["area"] if u["perfil"] == "analista" else None)
    return g.radar


@app.context_processor
def variaveis_globais():
    """Disponível em todos os templates."""
    u = usuario_atual()
    alertas = len(radar_do_usuario(u)["alertas"]) if u else 0
    return {"usuario": u, "telas": TELAS, "csrf": session.get("csrf", ""), "n_alertas": alertas}


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
    onde, params = filtro_area(u)
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
    onde, params = filtro_area(usuario)
    with banco.conectar() as c:
        compras = c.execute(f"SELECT * FROM compras WHERE {onde} ORDER BY data_pedido DESC", params).fetchall()
        propostas = c.execute(
            f"SELECT * FROM cotacoes WHERE id_compra IN (SELECT id_compra FROM compras WHERE {onde})", params).fetchall()
        # "Consumo previsível" olha a empresa toda, não só a área
        vezes = dict(c.execute("SELECT codigo_item, COUNT(*) FROM compras GROUP BY codigo_item").fetchall())
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
    onde, params = filtro_area(usuario_atual())
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
    onde, params = filtro_area(usuario_atual())
    with banco.conectar() as c:
        compras = c.execute(f"SELECT * FROM compras WHERE {onde}", params).fetchall()
        todas = c.execute("SELECT * FROM compras").fetchall()
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
                flash("Requisição criada." if cartao["tipo"] == "planejada" else "Estoque mínimo registrado.")
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


@app.route("/")
def inicio():
    return redirect(url_for("cotacoes"))


if __name__ == "__main__":
    app.run(debug=True)
