# Primeiros passos

Walkthrough end-to-end: do `pip install` até a primeira pergunta
respondida pelo agente, e depois pela API HTTP. ~10 minutos.

> Este guia mistura comandos em inglês (CLI) com prompts em pt-BR. A
> ideia é refletir como você vai usar a ferramenta na prática.

## 1. Instalação

HARA é distribuído no PyPI. O extra `[all]` puxa tudo (FastAPI, asyncpg,
chromadb, langchain providers, docling). Para instalações mais enxutas,
use só os extras que você precisa:

```bash
# tudo (recomendado para começar):
pip install hara[all]

# ou seleção fina:
pip install hara[api]            # FastAPI + uvicorn (necessário para `hara serve`)
pip install hara[postgres]       # asyncpg para o connector Postgres
pip install hara[chromadb]       # ChromaDB como vector store
pip install hara[anthropic]      # langchain-anthropic
pip install hara[google]         # langchain-google-genai
pip install hara[docling]        # extração robusta de PDF/DOCX
```

Verifique:

```bash
$ hara version
hara 0.1.0
```

## 2. `hara init`

O wizard pede 7 escolhas e escreve `hara.toml` + `.env` no diretório
atual. Use `--non-interactive` para gerar defaults (Docker/CI).

```bash
$ hara init
Provider de LLM [openai]: openai
OPENAI_API_KEY (deixe vazio para skip): sk-************
Modelo HARD (planner/verifier) [gpt-5]: gpt-5
Modelo SOFT (sql/synthesizer) [gpt-5-mini]: gpt-5-mini
Provider de embeddings [openai]: openai
Modelo de embeddings [text-embedding-3-small]: text-embedding-3-small
Connector SQL (memory/sqlite/postgres) [sqlite]: sqlite
Caminho do banco SQLite [./data/hara.db]: ./data/hara.db
Connector vector (memory/chromadb) [chromadb]: chromadb
Persist dir do ChromaDB [./data/chroma]: ./data/chroma
Gerar token de auth aleatório? [Y/n]: Y
token: 7K2m_b3nQ-vJxLmH1pAa9qZqYw7TtRnG2v8FwQv-aNs
Tom de voz do agente [Responda de forma direta...]: <enter>

✓ wrote hara.toml
✓ wrote secrets to .env

Next: hara doctor to verify, then hara serve.
```

Estrutura gerada:

```
.
├── hara.toml         # config principal — sem segredos
├── .env              # OPENAI_API_KEY, HARA_API_TOKEN
└── data/             # criado on-demand pelos connectors
```

> **Dica:** se já tiver um `hara.toml` e quiser regenerar, `hara init
> --force`.

## 3. `hara doctor`

Diagnóstico sanity. Roda checks de connectors, providers, paths e auth:

```bash
$ hara doctor
                        HARA — diagnóstico
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                         ┃ Status ┃ Detail                 ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━┩
│ auth.token                    │   OK   │ token set              │
│ provider.openai               │   OK   │ OPENAI_API_KEY ok      │
│ embeddings.openai             │   OK   │ embedding 3-small      │
│ connector.sql (sqlite)        │   OK   │ sqlite ready at        │
│                               │        │ data/hara.db           │
│ connector.vector (chromadb)   │   OK   │ chroma persist ok      │
│ connector.session_store       │   OK   │ inherits sql           │
│ paths.data_dir                │   OK   │ ./data writeable       │
└───────────────────────────────┴────────┴────────────────────────┘
all checks passed.
```

Qualquer linha **FAIL** explica exatamente o que falta (ex.: variável
de ambiente não setada, path read-only).

## 4. Dados de exemplo

Para o walkthrough abaixo, vamos usar dados fictícios de produção
agrícola. Crie o diretório e dois arquivos:

```bash
mkdir -p data/raw
```

`data/raw/conab_safras.csv` (cinco linhas dummies):

```csv
uf,cultura,ano,producao_toneladas,area_hectares
MT,soja,2023,42500000,11500000
PR,soja,2023,21800000,5700000
RS,soja,2023,11200000,5900000
GO,soja,2023,15300000,4100000
MT,milho,2023,46800000,7200000
```

`data/raw/manejo.pdf` — pode ser qualquer PDF curto sobre manejo
agronômico (alternativa: salve como `data/raw/manejo.md`).

> **Substitua pelos seus dados** se preferir. O resto do walkthrough
> presume `data/raw/<seus arquivos>` com pelo menos um CSV e um PDF/MD.

## 5. `hara ingest`

Lê o diretório, particiona por tipo, e indexa:

- `*.csv` → tabelas no SQL connector (uma por arquivo).
- `*.pdf|*.md|*.docx|*.txt` → chunks no vector connector.

```bash
$ hara ingest --from data/raw
[CSV  1/1] conab_safras.csv ✓ 5 rows → table "conab_safras"
[TEXT 1/1] manejo.pdf       ✓ 12 chunks → vector store
ingest finished in 4.3s.

resumo:
  CSV files indexed:  1
  text files indexed: 1
  total chunks:       12
```

`hara ingest --help` lista flags como `--mode replace|append|skip`,
`--workers N`, `--only csv` / `--only text`.

## 6. `hara semantic-map`

Gera dois YAMLs em `data/`:

- `data/structured.yaml` — schema + estatísticas de coluna por tabela
  SQL (descrição inferida via LLM).
- `data/unstructured.yaml` — uma entrada por documento com o resumo
  centroide-representativo dos chunks.

```bash
$ hara semantic-map
✓ structured.yaml: 1 tabela, 5 colunas
✓ unstructured.yaml: 1 documento
gerados em data/.
```

O agente carrega esses YAMLs no Planner e SQL Executor — eles são o
"manual" que o LLM usa para decidir quais tabelas/documentos consultar.

> **Dica:** com `[semantic_map].regenerate_on_ingest = true` (default),
> `hara ingest` já chama `semantic-map` no fim. Você só roda manual se
> mexer manualmente nos descritivos.

## 7. `hara chat`

One-shot, com streaming Markdown:

```bash
$ hara chat "Qual a produção de soja em 2023 por UF?"
⠋ Pensando…
A produção de soja em 2023 lidera com **Mato Grosso (42,5 Mt)** <ref:1>,
seguido por Paraná (21,8 Mt) <ref:1>, Goiás (15,3 Mt) <ref:1> e Rio
Grande do Sul (11,2 Mt) <ref:1>. Os números refletem a safra registrada
na tabela `conab_safras` <ref:1>.

                              Citações
┏━━━━━━━━━┳━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ ref     ┃ kind┃ source                   ┃ snippet                    ┃
┡━━━━━━━━━╇━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ <ref:1> │ sql │ conab_safras             │ SELECT uf, producao_t…     │
└─────────┴─────┴──────────────────────────┴────────────────────────────┘
subqueries=1 | routes=sql
thread_id: thr_8f3a2c
```

REPL mode (sem argumento):

```bash
$ hara chat
hara chat — REPL. /exit para sair.
> Qual a produção de soja em 2023?
[resposta…]
> E o milho?                          # mesma thread, agente lembra do contexto
[resposta…]
> /exit
```

## 8. `hara serve`

Sobe a API FastAPI:

```bash
$ hara serve
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

Forçado a `--workers=1` (spec §6: turns são `asyncio.Task` em memória
local do processo; multi-worker perderia visibilidade).

Em outro terminal, exporte token + URL base:

```bash
export BASE=http://localhost:8000
export TOKEN=$(grep HARA_API_TOKEN .env | cut -d= -f2)
```

## 9. `POST /invoke` (síncrono)

Conveniência: cria thread se necessário, roda um turno, devolve o
resultado completo.

```bash
$ curl -s -X POST $BASE/invoke \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"message": "Qual a produção de soja por UF em 2023?"}' | jq
{
  "turn_id": "trn_4a1b2c3d",
  "thread_id": "thr_e5f6g7h8",
  "answer": "A produção de soja em 2023 lidera com Mato Grosso (42,5 Mt) <ref:1>...",
  "citations": [
    {
      "evidence_id": 1,
      "kind": "sql",
      "source": "conab_safras",
      "section": "",
      "snippet": "SELECT uf, producao_toneladas FROM conab_safras WHERE ano = 2023..."
    }
  ],
  "metadata": {
    "routing": {"subqueries_count": 1, "routes_taken": ["sql"]},
    "unsupported_markers": 0
  }
}
```

## 10. Stream de eventos SSE

Para receber tokens à medida que o LLM gera (e ver o pipeline em
tempo real), use o endpoint SSE. Em terminal, **lembre de `--no-buffer`**
(senão `curl` segura tudo até EOF):

```bash
# dispara turn assíncrono (devolve turn_id em <50ms):
$ curl -s -X POST $BASE/threads/$THREAD_ID/messages \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"message":"Qual a produção de soja por UF?"}' | jq
{ "turn_id": "trn_x", "thread_id": "thr_y", "status": "running",
  "started_at": 1714658400.123 }

# consome o stream de eventos:
$ curl -N --no-buffer \
    -H "Authorization: Bearer $TOKEN" \
    "$BASE/threads/$THREAD_ID/turns/trn_x/events"
event: state
data: {"node":"planner","status":"start"}

event: state
data: {"node":"planner","status":"end"}

event: state
data: {"node":"sql_executor","status":"start"}

event: state
data: {"node":"sql_executor","status":"end","rows":4}

event: state
data: {"node":"synthesizer","status":"start"}

event: token
data: {"text":"A produção"}

event: token
data: {"text":" de soja"}

...

event: final
data: {"answer":"A produção de soja em 2023...","citations":[...],"metadata":{...}}
```

Os eventos são **persistidos**: se o cliente cair e reconectar, o mesmo
endpoint replaya tudo desde o início e depois continua tail-ing.

Alternativa **single-call streaming** sem precisar de thread/turn id
prévios (`POST /stream`):

```bash
curl -N --no-buffer -X POST $BASE/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"Qual a produção de soja por UF?"}'
```

Mesmos eventos, mas a thread/turn ficam expostas só nos campos do
evento `final`.

## Próximos passos

- [Configuration reference](configuration.md) — cada bloco do
  `hara.toml` explicado, defaults e quando mexer.
- [API reference](api.md) — todos os endpoints, schemas, códigos de
  erro, exemplos curl + clientes JS/Python.
- [Extending: connectors](extending/connectors.md) — implementar
  um connector MySQL passo a passo.

Dúvidas: abra uma issue. Bug: PR welcome.
