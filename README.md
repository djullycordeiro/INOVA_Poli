# JustPlan

**Toda compra ganha uma nota. Toda emergência previsível vira planejamento.**

Plataforma de suprimentos criada para o **INOVA POLI 2026** (desafios 8 e 9 da Natto Alimentos).
A JustPlan dá uma nota de 0 a 100 a cada compra e avisa antes da próxima compra emergencial.

Equipe: Djully Cordeiro e Giulia Sousa, Engenharia da Computação (POLI-UPE).

---

## O que o sistema faz

| Tela | Para que serve |
|---|---|
| 1. Cotações | Compara propostas pelo **custo de hoje** (preço × quantidade + frete, trazido a valor presente com 1% a.m.) |
| 2. Nota da Compra | Nota verde, amarela ou vermelha para cada compra, com os motivos e uma dica |
| 3. Histórico de preços | Preço de cada item ao longo do ano e quanto se pagou a mais nas emergências |
| 4. Savings | Economia na negociação × perda nas compras emergenciais, mês a mês |
| 5. Radar de Emergenciais | Detecta padrões (categoria, área, item) e sugere requisições antes da emergência |
| 6. Planos | Simulador de retorno do modelo híbrido (R$ 1.490/mês + 10% da economia) |
| Requisições | Analista pede a compra → gerente aprova → cotações → compra registrada |
| Importar compras | Gerente envia planilha (.xlsx ou .csv) com várias compras de uma vez |
| Bases de dados | Gerente alterna entre a base **Pitch** (fictícia) e a base **Natto** (real) |
| Convites | Gerente gera códigos de convite: só entra quem foi convidado |

**Perfis:** o *analista* vê só a própria área; o *gerente* vê todas as áreas, aprova requisições,
importa planilhas, troca a base e gera convites.

### Regra da nota

Começa em 100 e perde pontos:

| Motivo | Pontos |
|---|---|
| Preço mais de 10% acima do preço médio planejado do item | −40 |
| Preço entre 3% e 10% acima | −15 |
| Ignorou a proposta com melhor custo de hoje | −30 |
| Só 1 fornecedor cotado | −20 |
| Só 2 fornecedores cotados | −5 |
| Compra emergencial | −15 |
| Fora do orçamento | −5 |

Verde de 75 a 100, amarela de 50 a 74, vermelha abaixo de 50.

---

## Como rodar no seu computador

Precisa do **Python 3.10 ou mais novo** ([python.org](https://www.python.org/downloads/)).

```bash
git clone https://github.com/djullycordeiro/INOVA_Poli.git
cd INOVA_Poli
python -m venv venv
```

Ative o ambiente virtual:

- Windows: `venv\Scripts\activate`
- Mac/Linux: `source venv/bin/activate`

Instale, crie o banco e o primeiro gerente:

```bash
pip install -r requirements.txt
python banco.py iniciar
python banco.py criar-gerente
python app.py
```

Abra **http://localhost:5000** e entre com o e-mail e a senha do gerente.
Para convidar alguém, vá em **Convites**, gere um código e envie para a pessoa criar a conta em "Criar conta".

### Comandos úteis

| Comando | O que faz |
|---|---|
| `python banco.py iniciar` | Cria os bancos e (re)importa as 200 compras fictícias na base Pitch. Não apaga usuários nem o que foi lançado pelo site |
| `python banco.py criar-gerente` | Cria uma conta de gerente |
| `python banco.py limpar-demo` | Volta a base Pitch ao estado original do pitch (apaga compras e requisições de teste e desfaz edições) |
| `python banco.py usar natto` / `usar pitch` | Troca a base ativa pelo terminal (o gerente também troca pelo site) |

---

## As duas bases de dados

```
justplan_contas.db  → usuários, convites e qual base está ativa (vale para as duas)
justplan_pitch.db   → 200 compras fictícias da apresentação (out/2025 a set/2026)
justplan_natto.db   → começa vazia; recebe os dados reais da validação
```

- **Antes da validação com a Natto:** em *Bases de dados*, ative a base **Natto** e importe a planilha.
- **Antes do pitch:** volte para a base **Pitch**. Os números da apresentação (135 verdes, 30 amarelas,
  35 vermelhas) estão intactos.
- A base em uso aparece sempre na barra lateral.
- As telas mostram sempre **os 12 meses até a compra mais recente** (período móvel).

## Importar compras por planilha

Em **Importar compras**, baixe a planilha modelo. Ela tem três abas:

- **compras** (obrigatória). Colunas obrigatórias: data do pedido, área, item, quantidade, fornecedor,
  preço unitário pago e tipo (planejada/emergencial).
- **cotacoes** (opcional): as propostas concorrentes de cada pedido, para comparar pelo custo de hoje.
- **instrucoes**: o que cada coluna significa e os outros nomes aceitos.

A planilha não precisa ser idêntica ao modelo: "Data do pedido", "Qtd", "Preço unitário", "Setor"
e outras variações são reconhecidas. Números e datas podem estar no formato brasileiro
(`R$ 1.234,56`, `05/11/2026`). Antes de gravar, a JustPlan mostra uma **prévia** com os erros de cada linha.

Campos que a empresa normalmente não tem (custo de hoje, economia, preço médio planejado,
pago a mais) são **calculados automaticamente**.

---

## Estrutura do código

```
app.py            ← as páginas (rotas) do site
regras.py         ← todas as contas: custo de hoje, nota, savings, radar, simulador
banco.py          ← bancos de dados e comandos de terminal
importacao.py     ← leitura e conferência de planilhas
seguranca.py      ← login, CSRF, bloqueio de tentativas, permissões, cabeçalhos
wsgi.py           ← ponto de entrada para hospedagem
templates/        ← HTML das telas (Jinja)
static/           ← CSS e logo
dados/            ← CSVs fictícios da base Pitch
```

## Segurança

- Senhas guardadas com hash (nunca em texto).
- Cadastro só com **código de convite** individual, de uso único, que expira em 7 dias.
- Bloqueio de 15 minutos após 5 senhas erradas.
- Proteção contra CSRF em todos os formulários.
- Analista só acessa dados da própria área (conferido no servidor, não só na tela).
- Toda edição de compra exige motivo e fica registrada (quem, quando, antes e depois).
- Cookie de login só por HTTPS no site publicado; cabeçalhos de segurança em todas as respostas.

### ⚠️ Dados reais

Os bancos (`*.db`), a pasta `uploads/` e o arquivo `.chave_secreta` **estão no `.gitignore` e nunca
devem ir para o GitHub**. O repositório guarda apenas os dados fictícios da pasta `dados/`.
Antes de colocar dados reais de uma empresa num site online, confirme a autorização dela por escrito
(e lembre da LGPD quando houver nomes de pessoas, como compradores).

---

## Colocar no ar (PythonAnywhere, plano gratuito)

1. Crie uma conta em [pythonanywhere.com](https://www.pythonanywhere.com) (plano *Beginner*, grátis).
2. Abra um **Bash console** e rode:
   ```bash
   git clone https://github.com/djullycordeiro/INOVA_Poli.git
   cd INOVA_Poli
   python3.10 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   python banco.py iniciar
   python banco.py criar-gerente
   ```
3. Na aba **Web**, clique em *Add a new web app* → *Manual configuration* → Python 3.10.
4. Em **Virtualenv**, informe `/home/SEU_USUARIO/INOVA_Poli/venv`.
5. Clique no arquivo **WSGI configuration file** e troque todo o conteúdo por:
   ```python
   import sys
   sys.path.insert(0, "/home/SEU_USUARIO/INOVA_Poli")
   from wsgi import application
   ```
6. Em **Static files**, adicione: URL `/static/` → Directory `/home/SEU_USUARIO/INOVA_Poli/static`.
7. Em **Security**, ligue **Force HTTPS**.
8. Clique em **Reload**. O site fica em `https://SEU_USUARIO.pythonanywhere.com`.

Para atualizar depois de um novo commit: no console, `cd INOVA_Poli && git pull`, e clique em **Reload** na aba Web.
No plano gratuito, é preciso clicar em "Run until 3 months from today" na aba Web a cada 3 meses.

---

Protótipo INOVA POLI 2026 · Dados da base Pitch são fictícios.
