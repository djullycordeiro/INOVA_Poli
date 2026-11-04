"""
app.py — o site. Cada função com @app.route é uma página.

Para rodar:
    python app.py      -> abre em http://localhost:5000
"""

from datetime import datetime, timedelta, timezone

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import banco
from seguranca import (configurar, filtro_area, limpar_erros_login, login_bloqueado,
                       login_obrigatorio, novo_codigo_convite, registrar_erro_login,
                       so_gerente, usuario_atual)

app = Flask(__name__)
configurar(app)

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


@app.context_processor
def variaveis_globais():
    """Disponível em todos os templates."""
    return {"usuario": usuario_atual(), "telas": TELAS, "csrf": session.get("csrf", "")}


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
            return redirect(url_for("tela", nome="cotacoes"))
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
# As 6 telas (passo 3 vai preencher cada uma)
# ---------------------------------------------------------------------------
@app.route("/")
def inicio():
    return redirect(url_for("tela", nome="cotacoes"))


@app.route("/<nome>")
@login_obrigatorio
def tela(nome):
    titulos = dict(TELAS)
    if nome not in titulos:
        abort(404)
    u = usuario_atual()
    onde, params = filtro_area(u)
    with banco.conectar() as c:
        total = c.execute(f"SELECT COUNT(*) FROM compras WHERE {onde}", params).fetchone()[0]
    passo = [t[0] for t in TELAS].index(nome) + 1
    return render_template("em_construcao.html", nome=nome, titulo=titulos[nome], passo=passo, total=total)


if __name__ == "__main__":
    app.run(debug=True)
