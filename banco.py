"""
banco.py — tudo que mexe com o banco de dados (SQLite).

O banco é um arquivo só: justplan.db (ele NÃO vai pro GitHub, está no .gitignore).

Comandos de terminal:
    python banco.py iniciar         -> cria as tabelas e importa os CSVs da pasta dados/
    python banco.py criar-gerente   -> cria a primeira conta de gerente (pede e-mail e senha)
"""

import csv
import getpass
import sqlite3
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

PASTA = Path(__file__).parent
ARQUIVO_BANCO = PASTA / "justplan.db"
PASTA_DADOS = PASTA / "dados"


def conectar():
    """Abre uma conexão com o banco. Cada linha volta como um 'dicionário' (linha["coluna"])."""
    conexao = sqlite3.connect(ARQUIVO_BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


# ---------------------------------------------------------------------------
# Estrutura das tabelas
# ---------------------------------------------------------------------------
ESTRUTURA = """
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

CREATE TABLE IF NOT EXISTS requisicoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chave       TEXT NOT NULL,            -- qual cartão do Radar gerou (ex.: planejada:EPI-001)
    tipo        TEXT NOT NULL CHECK (tipo IN ('planejada', 'estoque_minimo')),
    titulo      TEXT NOT NULL,
    itens       TEXT NOT NULL,            -- códigos dos itens, separados por vírgula
    area        TEXT NOT NULL,
    data_limite TEXT,                     -- até quando o pedido precisa sair
    criado_por  INTEGER NOT NULL REFERENCES usuarios(id),
    criado_em   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS compras (
    id_compra TEXT PRIMARY KEY, data_pedido TEXT, area_solicitante TEXT, categoria TEXT,
    codigo_item TEXT, item TEXT, unidade TEXT, quantidade REAL, fornecedor TEXT, comprador TEXT,
    qtd_cotacoes INTEGER, tipo_compra TEXT, motivo_emergencia TEXT,
    preco_unit_inicial REAL, preco_unit_final REAL, valor_itens REAL, frete REAL, custo_total REAL,
    prazo_pagamento_dias INTEGER, custo_total_hoje REAL, saving_negociacao REAL,
    preco_medio_planejado_item REAL, pago_a_mais_vs_planejado REAL, previsto_no_orcamento TEXT,
    prazo_entrega_dias INTEGER, entregue_no_prazo TEXT, modal_transporte TEXT,
    transportadora TEXT, inspecao_recebimento TEXT
);

CREATE TABLE IF NOT EXISTS cotacoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_compra   TEXT NOT NULL REFERENCES compras(id_compra),
    fornecedor TEXT, preco_unit REAL, quantidade REAL, valor_itens REAL, frete REAL,
    custo_total REAL, prazo_pagamento_dias INTEGER, custo_total_hoje REAL,
    prazo_entrega_dias INTEGER, vencedora TEXT, melhor_custo_hoje TEXT
);
"""

# Colunas que são números (o CSV traz tudo como texto)
NUMEROS = {
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


def garantir_tabelas():
    """Cria as tabelas que faltarem (não apaga nada). Chamado quando o site liga."""
    with conectar() as conexao:
        conexao.executescript(ESTRUTURA)


def iniciar():
    """Cria as tabelas e (re)importa compras e cotações. Usuários e convites são preservados."""
    with conectar() as conexao:
        conexao.executescript(ESTRUTURA)
        conexao.execute("DELETE FROM cotacoes")
        conexao.execute("DELETE FROM compras")
        n_compras = _importar_csv(conexao, PASTA_DADOS / "natto_compras.csv", "compras")
        n_cot = _importar_csv(conexao, PASTA_DADOS / "natto_cotacoes.csv", "cotacoes")
    print(f"Banco pronto: {n_compras} compras e {n_cot} cotações importadas.")


def areas():
    """Lista as áreas que existem na base de compras."""
    with conectar() as conexao:
        return [l["area_solicitante"] for l in conexao.execute(
            "SELECT DISTINCT area_solicitante FROM compras ORDER BY area_solicitante")]


def criar_gerente():
    """Cria um gerente pelo terminal. A senha é digitada escondida e nunca aparece na tela."""
    nome = input("Nome: ").strip()
    email = input("E-mail: ").strip().lower()
    senha = getpass.getpass("Senha (mín. 8 caracteres): ")
    if len(senha) < 8:
        sys.exit("Senha curta demais.")
    if senha != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    with conectar() as conexao:
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
    else:
        print(__doc__)
