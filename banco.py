"""
banco.py — tudo que mexe com o banco de dados (SQLite).

São 3 arquivos (nenhum vai pro GitHub, *.db está no .gitignore):
    justplan_contas.db  -> usuários, convites e qual base está ativa (vale para as duas bases)
    justplan_pitch.db   -> base "Pitch": as 200 compras fictícias da apresentação
    justplan_natto.db   -> base "Natto": começa vazia; recebe os dados reais da validação

Cada conexão abre a base ativa e "anexa" o banco de contas. Por isso uma consulta como
"SELECT ... FROM requisicoes JOIN usuarios" funciona: requisicoes vem da base ativa e
usuarios vem das contas.

Comandos de terminal:
    python banco.py iniciar           -> cria tudo e importa os CSVs da pasta dados/ na base Pitch
    python banco.py criar-gerente     -> cria a primeira conta de gerente (pede e-mail e senha)
    python banco.py limpar-demo       -> apaga compras e requisições lançadas pelo site na base Pitch
    python banco.py usar pitch|natto  -> troca a base ativa (o gerente também troca pelo site)
"""

import csv
import getpass
import sqlite3
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

PASTA = Path(__file__).parent
PASTA_DADOS = PASTA / "dados"
ARQUIVO_CONTAS = PASTA / "justplan_contas.db"
BASES = {
    "pitch": {"arquivo": PASTA / "justplan_pitch.db", "nome": "Pitch", "descricao": "dados fictícios da apresentação"},
    "natto": {"arquivo": PASTA / "justplan_natto.db", "nome": "Natto", "descricao": "dados reais da validação"},
}
BASE_PADRAO = "pitch"

# Áreas da Natto. Servem para convites e requisições mesmo quando a base ainda está vazia.
AREAS_PADRAO = [
    "Administrativo", "Industrializados", "Logística e Expedição", "Manutenção Industrial",
    "Produção (Abate e Cortes)", "Qualidade e Laboratório",
]


# ---------------------------------------------------------------------------
# Estrutura das tabelas
# ---------------------------------------------------------------------------
ESTRUTURA_CONTAS = """
CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    senha_hash  TEXT NOT NULL,            -- a senha embaralhada, nunca a senha em si
    perfil      TEXT NOT NULL CHECK (perfil IN ('gerente', 'analista')),
    area        TEXT,                     -- vazio para gerente (vê todas as áreas)
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS convites (
    codigo      TEXT PRIMARY KEY,         -- ex.: QWRS-N34J-5KXP
    perfil      TEXT NOT NULL CHECK (perfil IN ('gerente', 'analista')),
    area        TEXT,                     -- área do analista convidado
    criado_por  INTEGER NOT NULL REFERENCES usuarios(id),
    criado_em   TEXT NOT NULL DEFAULT (datetime('now')),
    expira_em   TEXT NOT NULL,
    usado_por   INTEGER REFERENCES usuarios(id),
    usado_em    TEXT,
    cancelado   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config (
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""

# Tabelas de cada base. Quem criou/decidiu guarda o id do usuário (que mora no banco de contas;
# por isso não há REFERENCES para usuarios aqui: o SQLite não liga chaves entre arquivos).
ESTRUTURA_BASE = """
CREATE TABLE IF NOT EXISTS requisicoes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chave           TEXT NOT NULL,        -- quem gerou: 'manual:...' ou o cartão do Radar (ex.: planejada:EPI-001)
    tipo            TEXT NOT NULL CHECK (tipo IN ('planejada', 'estoque_minimo')),
    titulo          TEXT NOT NULL,
    itens           TEXT NOT NULL,        -- códigos dos itens, separados por vírgula
    area            TEXT NOT NULL,
    data_limite     TEXT,                 -- até quando o pedido precisa sair (cartões do Radar)
    quantidade      REAL,
    data_necessaria TEXT,                 -- quando o material precisa chegar
    urgencia        TEXT NOT NULL DEFAULT 'Planejada',
    justificativa   TEXT,
    status          TEXT NOT NULL DEFAULT 'aberta',   -- aberta, aprovada, recusada, concluida
    criado_por      INTEGER NOT NULL,
    criado_em       TEXT NOT NULL DEFAULT (datetime('now')),
    decidido_por    INTEGER,
    decidido_em     TEXT,
    motivo_recusa   TEXT,
    id_compra       TEXT                  -- a compra que concluiu a requisição
);

CREATE TABLE IF NOT EXISTS propostas (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    requisicao_id        INTEGER NOT NULL REFERENCES requisicoes(id),
    fornecedor           TEXT NOT NULL,
    preco_unit           REAL NOT NULL,
    frete                REAL NOT NULL DEFAULT 0,
    prazo_pagamento_dias INTEGER NOT NULL DEFAULT 0,
    prazo_entrega_dias   INTEGER NOT NULL,
    criado_por           INTEGER NOT NULL,
    criado_em            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS compras (
    id_compra TEXT PRIMARY KEY, data_pedido TEXT, area_solicitante TEXT, categoria TEXT,
    codigo_item TEXT, item TEXT, unidade TEXT, quantidade REAL, fornecedor TEXT, comprador TEXT,
    qtd_cotacoes INTEGER, tipo_compra TEXT, motivo_emergencia TEXT,
    preco_unit_inicial REAL, preco_unit_final REAL, valor_itens REAL, frete REAL, custo_total REAL,
    prazo_pagamento_dias INTEGER, custo_total_hoje REAL, saving_negociacao REAL,
    preco_medio_planejado_item REAL, pago_a_mais_vs_planejado REAL, previsto_no_orcamento TEXT,
    prazo_entrega_dias INTEGER, entregue_no_prazo TEXT, modal_transporte TEXT,
    transportadora TEXT, inspecao_recebimento TEXT,
    origem        TEXT NOT NULL DEFAULT 'csv',   -- 'csv' (pitch), 'site' (registrada no site), 'planilha' (importada)
    requisicao_id INTEGER
);

CREATE TABLE IF NOT EXISTS cotacoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_compra   TEXT NOT NULL REFERENCES compras(id_compra),
    fornecedor TEXT, preco_unit REAL, quantidade REAL, valor_itens REAL, frete REAL,
    custo_total REAL, prazo_pagamento_dias INTEGER, custo_total_hoje REAL,
    prazo_entrega_dias INTEGER, vencedora TEXT, melhor_custo_hoje TEXT
);

CREATE TABLE IF NOT EXISTS alteracoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_compra   TEXT NOT NULL,
    campo       TEXT NOT NULL,
    antes       TEXT,
    depois      TEXT,
    motivo      TEXT NOT NULL,
    alterado_por INTEGER NOT NULL,
    alterado_em TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ---------------------------------------------------------------------------
# Conexões
# ---------------------------------------------------------------------------
def conectar_contas():
    conexao = sqlite3.connect(ARQUIVO_CONTAS)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def base_ativa():
    """'pitch' ou 'natto', guardado no banco de contas."""
    try:
        with conectar_contas() as c:
            linha = c.execute("SELECT valor FROM config WHERE chave = 'base_ativa'").fetchone()
    except sqlite3.OperationalError:   # tabela ainda não existe
        return BASE_PADRAO
    return linha["valor"] if linha and linha["valor"] in BASES else BASE_PADRAO


def definir_base(base):
    if base not in BASES:
        raise ValueError(f"Base desconhecida: {base}")
    with conectar_contas() as c:
        c.execute("INSERT INTO config (chave, valor) VALUES ('base_ativa', ?) "
                  "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (base,))


def conectar(base=None):
    """Abre a base ativa (ou a indicada) com o banco de contas anexado.
    Cada linha volta como um 'dicionário' (linha["coluna"])."""
    conexao = sqlite3.connect(BASES[base or base_ativa()]["arquivo"])
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("ATTACH DATABASE ? AS contas", (str(ARQUIVO_CONTAS),))
    return conexao


# ---------------------------------------------------------------------------
# Criação e importação
# ---------------------------------------------------------------------------
NUMEROS = {   # colunas que são números (o CSV traz tudo como texto)
    "quantidade", "qtd_cotacoes", "preco_unit_inicial", "preco_unit_final", "valor_itens",
    "frete", "custo_total", "prazo_pagamento_dias", "custo_total_hoje", "saving_negociacao",
    "preco_medio_planejado_item", "pago_a_mais_vs_planejado", "prazo_entrega_dias", "preco_unit",
}


def _converter(coluna, valor):
    if valor == "":
        return None
    return float(valor) if coluna in NUMEROS else valor


def _importar_csv(conexao, arquivo, tabela):
    with open(arquivo, encoding="utf-8") as f:
        linhas = list(csv.DictReader(f))
    colunas = list(linhas[0].keys())
    sql = f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({', '.join('?' * len(colunas))})"
    conexao.executemany(sql, [[_converter(c, l[c]) for c in colunas] for l in linhas])
    return len(linhas)


def _migrar_banco_antigo():
    """Versões anteriores usavam um arquivo só (justplan.db). Se ele existir e as contas ainda
    não, copia usuários e convites para o novo banco de contas (o resto é reimportado)."""
    antigo = PASTA / "justplan.db"
    if not antigo.exists():
        return
    with conectar_contas() as c:
        if c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]:
            return
        c.execute("ATTACH DATABASE ? AS antigo", (str(antigo),))
        tabelas = {l[0] for l in c.execute("SELECT name FROM antigo.sqlite_master WHERE type = 'table'")}
        if "usuarios" in tabelas:
            c.execute("INSERT INTO usuarios SELECT * FROM antigo.usuarios")
        if "convites" in tabelas:
            c.execute("INSERT INTO convites SELECT * FROM antigo.convites")


def garantir_tabelas():
    """Cria os arquivos e tabelas que faltarem (não apaga nada). Chamado quando o site liga."""
    with conectar_contas() as c:
        c.executescript(ESTRUTURA_CONTAS)
    _migrar_banco_antigo()
    for base in BASES:
        conexao = sqlite3.connect(BASES[base]["arquivo"])
        with conexao:
            conexao.executescript(ESTRUTURA_BASE)
        conexao.close()


def iniciar():
    """Cria tudo e (re)importa as compras e cotações dos CSVs na base Pitch.
    Só apaga o que veio do CSV: usuários, convites, requisições e compras do site são preservados."""
    garantir_tabelas()
    with conectar("pitch") as conexao:
        conexao.execute("DELETE FROM cotacoes WHERE id_compra IN (SELECT id_compra FROM compras WHERE origem = 'csv')")
        conexao.execute("DELETE FROM compras WHERE origem = 'csv'")
        n_compras = _importar_csv(conexao, PASTA_DADOS / "natto_compras.csv", "compras")
        n_cot = _importar_csv(conexao, PASTA_DADOS / "natto_cotacoes.csv", "cotacoes")
    print(f"Base Pitch pronta: {n_compras} compras e {n_cot} cotações importadas. Base Natto criada (vazia).")


def apagar_movimento(conexao, so_do_site):
    """Apaga requisições, propostas e compras lançadas (pelo site, ou todas se so_do_site=False)."""
    filtro = "WHERE origem != 'csv'" if so_do_site else ""
    conexao.execute("UPDATE requisicoes SET id_compra = NULL")   # desfaz a ligação requisição <-> compra
    conexao.execute(f"DELETE FROM cotacoes WHERE id_compra IN (SELECT id_compra FROM compras {filtro})")
    conexao.execute(f"DELETE FROM alteracoes WHERE id_compra IN (SELECT id_compra FROM compras {filtro})")
    conexao.execute(f"DELETE FROM compras {filtro}")
    conexao.execute("DELETE FROM propostas")
    conexao.execute("DELETE FROM requisicoes")


def limpar_demo(confirmar=True):
    """Volta a base Pitch ao estado da apresentação. Usuários, convites e a base Natto não mudam."""
    with conectar("pitch") as conexao:
        n = conexao.execute("SELECT COUNT(*) FROM compras WHERE origem != 'csv'").fetchone()[0]
        r = conexao.execute("SELECT COUNT(*) FROM requisicoes").fetchone()[0]
        e = conexao.execute("SELECT COUNT(DISTINCT id_compra) FROM alteracoes").fetchone()[0]
    aviso = f"Apagar {n} compra(s) lançada(s) e {r} requisição(ões) da base Pitch"
    aviso += f" e reimportar as {e} compra(s) editada(s)" if e else ""
    if confirmar and input(aviso + "? Digite SIM: ").strip() != "SIM":
        print("Nada foi apagado.")
        return
    with conectar("pitch") as conexao:
        apagar_movimento(conexao, so_do_site=True)
        conexao.execute("DELETE FROM alteracoes")
    iniciar()   # reimporta as 200 originais (desfaz edições)
    print("Pronto: a base Pitch voltou aos dados originais da apresentação.")


def areas():
    """Áreas da empresa: as padrão + as que aparecerem nas compras da base ativa."""
    with conectar() as conexao:
        nas_compras = {l[0] for l in conexao.execute("SELECT DISTINCT area_solicitante FROM compras") if l[0]}
    return sorted(set(AREAS_PADRAO) | nas_compras)


def criar_gerente():
    """Cria um gerente pelo terminal. A senha é digitada escondida e nunca aparece na tela."""
    garantir_tabelas()
    nome = input("Nome: ").strip()
    email = input("E-mail: ").strip().lower()
    senha = getpass.getpass("Senha (mín. 8 caracteres): ")
    if len(senha) < 8:
        sys.exit("Senha curta demais.")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    with conectar_contas() as conexao:
        conexao.execute(
            "INSERT INTO usuarios (nome, email, senha_hash, perfil, area) VALUES (?, ?, ?, 'gerente', NULL)",
            (nome, email, generate_password_hash(senha)),
        )
    print(f"Gerente {email} criado.")


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else ""
    if comando == "iniciar":
        iniciar()
    elif comando == "criar-gerente":
        criar_gerente()
    elif comando == "limpar-demo":
        limpar_demo()
    elif comando == "usar" and len(sys.argv) > 2:
        garantir_tabelas()
        definir_base(sys.argv[2])
        print(f"Base ativa: {BASES[sys.argv[2]]['nome']}")
    else:
        print(__doc__)
