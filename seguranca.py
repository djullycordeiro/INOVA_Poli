"""
seguranca.py — tudo que protege o site.

- Proteção CSRF: todo formulário leva um código secreto da sessão.
- Bloqueio de login: 5 senhas erradas em 15 minutos bloqueiam aquele e-mail.
- Porteiros (@login_obrigatorio e @so_gerente) que ficam em cima das páginas.
- Filtro por área: analista só vê a própria área.
"""

import os
import secrets
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import abort, redirect, request, session, url_for

import banco


def configurar(app):
    """Liga as proteções no site. Chamado uma vez no app.py."""
    # Chave que "assina" o cookie de login. Vem do .env/ambiente; se não houver,
    # é gerada uma vez e guardada no arquivo .chave_secreta (ignorado pelo Git).
    arquivo_chave = Path(__file__).parent / ".chave_secreta"
    if os.environ.get("CHAVE_SECRETA"):
        app.secret_key = os.environ["CHAVE_SECRETA"]
    else:
        if not arquivo_chave.exists():
            arquivo_chave.write_text(secrets.token_hex(32))
        app.secret_key = arquivo_chave.read_text()

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,      # JavaScript não consegue ler o cookie de login
        SESSION_COOKIE_SAMESITE="Lax",     # outros sites não enviam o cookie junto
        # Online (JUSTPLAN_PRODUCAO=1), o cookie só viaja por HTTPS. No computador local fica desligado.
        SESSION_COOKIE_SECURE=os.environ.get("JUSTPLAN_PRODUCAO") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    app.before_request(_protecao_csrf)
    app.after_request(_cabecalhos_de_seguranca)


def _cabecalhos_de_seguranca(resposta):
    """Instruções para o navegador que fecham brechas comuns."""
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")      # não "adivinhar" o tipo do arquivo
    resposta.headers.setdefault("X-Frame-Options", "DENY")                # o site não pode ser embutido em outro
    resposta.headers.setdefault("Referrer-Policy", "same-origin")         # não vazar endereços para outros sites
    if os.environ.get("JUSTPLAN_PRODUCAO") == "1":
        resposta.headers.setdefault("Strict-Transport-Security", "max-age=31536000")   # sempre HTTPS
    return resposta


def _protecao_csrf():
    """Se o código secreto do formulário não bater com o da sessão, recusa.
    Isso impede que um site falso envie formulários em nome do usuário logado."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST" and request.form.get("csrf") != session["csrf"]:
        abort(400)


# ---------------------------------------------------------------------------
# Bloqueio de tentativas de login
# ---------------------------------------------------------------------------
_tentativas = {}  # {email: [horários das tentativas erradas]}
MAX_TENTATIVAS, JANELA_SEGUNDOS = 5, 15 * 60


def login_bloqueado(email):
    agora = time.time()
    recentes = [t for t in _tentativas.get(email, []) if agora - t < JANELA_SEGUNDOS]
    _tentativas[email] = recentes
    return len(recentes) >= MAX_TENTATIVAS


def registrar_erro_login(email):
    _tentativas.setdefault(email, []).append(time.time())


def limpar_erros_login(email):
    _tentativas.pop(email, None)


# ---------------------------------------------------------------------------
# Quem está logado e o que pode ver
# ---------------------------------------------------------------------------
def usuario_atual():
    if "usuario_id" not in session:
        return None
    with banco.conectar() as c:
        return c.execute("SELECT * FROM usuarios WHERE id = ?", (session["usuario_id"],)).fetchone()


def login_obrigatorio(funcao):
    @wraps(funcao)
    def verificada(*args, **kwargs):
        if usuario_atual() is None:
            return redirect(url_for("login"))
        return funcao(*args, **kwargs)
    return verificada


def so_gerente(funcao):
    @wraps(funcao)
    def verificada(*args, **kwargs):
        u = usuario_atual()
        if u is None:
            return redirect(url_for("login"))
        if u["perfil"] != "gerente":
            abort(403)
        return funcao(*args, **kwargs)
    return verificada


def filtro_area(usuario):
    """Analista só vê a própria área; gerente vê tudo.
    Devolve um pedaço de SQL e os parâmetros, para usar em qualquer consulta de compras."""
    if usuario["perfil"] == "gerente":
        return "1 = 1", []
    return "area_solicitante = ?", [usuario["area"]]


def novo_codigo_convite():
    """Código tipo QWRS-N34J-5KXP. Sem letras/números parecidos (0/O, 1/I)."""
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    bruto = "".join(secrets.choice(alfabeto) for _ in range(12))
    return f"{bruto[:4]}-{bruto[4:8]}-{bruto[8:]}"
