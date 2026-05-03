# Referência de configuração

Tudo que vai no `hara.toml`. Cada bloco abaixo lista os campos
disponíveis, defaults, exemplos e — talvez o mais útil — uma coluna
"quando mexer" indicando se o default já serve no seu caso ou se você
provavelmente quer customizar.

> **Convenção:** segredos (API keys, tokens, URLs com senha) **nunca**
> ficam no `hara.toml`. Vão para `.env` (template em `.env.example`).
> Variáveis de ambiente sempre sobrescrevem o TOML — veja
> [Override por variáveis de ambiente](#override-por-variáveis-de-ambiente) no fim deste doc.

Layout esperado do arquivo (rodadas e ordem dos blocos seguem o
template em `hara.toml.example`):

```toml
[server]
[auth]
[api]
[api.cors]
[models]
[embeddings]
[providers.openai]
[providers.anthropic]
...
[connectors.sql]
[connectors.vector]
[connectors.session_store]
[paths]
[verifier]
[agent]
[ingest]
[semantic_map]
[logging]
```

---

## `[server]`

Onde a API HTTP escuta.

| Campo | Tipo   | Default     | Exemplo        | Quando mexer                                  |
|-------|--------|-------------|----------------|-----------------------------------------------|
| host  | string | `"0.0.0.0"` | `"127.0.0.1"`  | Restringir bind a localhost em dev local.     |
| port  | int    | `8000`      | `8080`         | Conflito de porta com outro serviço.          |

Pode também ser sobrescrito por `--host` / `--port` em `hara serve`.

---

## `[auth]`

| Campo | Tipo   | Default | Exemplo                                | Quando mexer       |
|-------|--------|---------|----------------------------------------|--------------------|
| token | string | `""`    | `"7K2m_b3nQ-vJxLmH1pAa9qZqYw7TtRnG"`   | Sempre (gere um!). |

Token Bearer único. **`hara serve` recusa subir se vazio.** Para gerar
um token criptograficamente seguro:

```python
>>> import secrets
>>> secrets.token_urlsafe(32)
'7K2m_b3nQ-vJxLmH1pAa9qZqYw7TtRnG2v8FwQv-aNs'
```

`hara init` faz isso por você se você responder "Y" no prompt do token.

> **Recomendado**: deixe `token = ""` no `hara.toml` e popule via env
> var `HARA_API_TOKEN` (que é mapeada automaticamente para
> `AUTH__TOKEN`). Assim o TOML pode ser commitado sem segredo.

---

## `[api]`

Comportamento da API HTTP.

| Campo                | Tipo | Default | Exemplo  | Quando mexer                                                                                       |
|----------------------|------|---------|----------|----------------------------------------------------------------------------------------------------|
| max_message_length   | int  | `8000`  | `16000`  | Perguntas muito longas; aumentar com cautela (custo de tokens).                                    |
| snippet_max_chars    | int  | `200`   | `500`    | Trecho de evidência na citação. Maior = mais contexto na UI, mais payload.                         |
| open_docs            | bool | `true`  | `false`  | **Production**: setar `false` para desabilitar `/docs` + `/openapi.json` (Swagger).                |

> **`open_docs` em deployment público:** mesmo que toda rota fora
> de `/health` esteja sob auth, o endpoint `/openapi.json` revela
> sua estrutura de schemas. Em produção, considere `false`.

---

## `[api.cors]`

CORS é desligado por padrão (lista vazia → middleware não monta).

| Campo            | Tipo          | Default | Exemplo                            | Quando mexer                                       |
|------------------|---------------|---------|------------------------------------|----------------------------------------------------|
| allowed_origins  | list[string]  | `[]`    | `["https://app.exemplo.com.br"]`   | Browser front-end servindo de outro domínio.       |

Quando setado, o middleware permite **apenas esses origins** + métodos
`GET, POST, DELETE, OPTIONS` + headers `Authorization, Content-Type`.

---

## `[models]`

Tier-defaults: o agente usa **hard** para tarefas que exigem raciocínio
(Planner, Verifier) e **soft** para tarefas mais mecânicas (SQL Executor,
Synthesizer). Override por nó é opcional.

| Campo        | Tipo                          | Default                   | Exemplo                             | Quando mexer                                                  |
|--------------|-------------------------------|---------------------------|-------------------------------------|---------------------------------------------------------------|
| hard         | `{provider, model}` (obrig.)  | —                         | `{provider="openai", model="gpt-5"}`| Trocar família/modelo do raciocínio.                          |
| soft         | `{provider, model}` (obrig.)  | —                         | `gpt-5-mini`                        | Reduzir custo da síntese / SQL.                               |
| planner      | `{provider, model}` ou ausente| `hard`                    | —                                   | Usar modelo específico só pro Planner.                        |
| sql          | `{provider, model}` ou ausente| `soft`                    | —                                   | Usar modelo específico só pro SQL Executor.                   |
| synthesizer  | `{provider, model}` ou ausente| `soft`                    | —                                   | Usar modelo específico só pro Synthesizer.                    |
| verifier     | `{provider, model}` ou ausente| `hard`                    | —                                   | Usar modelo específico só pro Verifier.                       |

Exemplo:

```toml
[models]
hard = { provider = "anthropic", model = "claude-opus-4-7" }
soft = { provider = "openai",    model = "gpt-5-mini"    }
synthesizer = { provider = "openai", model = "gpt-5"     } # override soft só na síntese
```

---

## `[embeddings]`

| Campo    | Tipo   | Default                      | Exemplo                  | Quando mexer                                       |
|----------|--------|------------------------------|--------------------------|----------------------------------------------------|
| provider | string | (obrigatório)                | `"openai"`               | Trocar provider de embeddings.                     |
| model    | string | (obrigatório)                | `"text-embedding-3-small"` | Modelo dentro do provider.                         |

Providers de embeddings disponíveis: `openai`, `google`, `ollama`.

> **Atenção**: trocar embeddings invalida o vector store. Re-rode
> `hara ingest` depois de mudar.

---

## `[providers.*]`

Um sub-bloco por provider que você usa. Para os hosted (`openai`,
`anthropic`, `google`, `openrouter`), basta declarar o bloco vazio
ou omitir; a chave vai no `.env`. Para os locais (`ollama`,
`lmstudio`), declare a `base_url`.

```toml
[providers.openai]
[providers.anthropic]
[providers.google]
[providers.openrouter]
[providers.ollama]
base_url = "http://localhost:11434/v1"
[providers.lmstudio]
base_url = "http://localhost:1234/v1"
```

| Provider     | Env var (auth)        | base_url default                 | Notas                                                          |
|--------------|-----------------------|----------------------------------|----------------------------------------------------------------|
| `openai`     | `OPENAI_API_KEY`      | (oficial)                        | Default para CSV/PDF dataset. Suporta gpt-5 / gpt-5-mini.      |
| `anthropic`  | `ANTHROPIC_API_KEY`   | (oficial)                        | Requer extra `pip install hara[anthropic]`.                    |
| `google`     | `GOOGLE_API_KEY`      | (oficial)                        | Requer extra `pip install hara[google]`.                       |
| `openrouter` | `OPENROUTER_API_KEY`  | (openrouter.ai)                  | Modelos de múltiplos providers via OpenAI-compatible API.      |
| `ollama`     | (none)                | `http://localhost:11434/v1`      | Self-hosted; modelos locais (Llama, Mistral, etc.).            |
| `lmstudio`   | (none)                | `http://localhost:1234/v1`       | Self-hosted via [LM Studio](https://lmstudio.ai).              |

---

## `[connectors.sql]`

Onde HARA persiste tabelas estruturadas (CSV ingerido). Tagged-union
discriminado por `type`.

### `type = "sqlite"` (recomendado para começar)

| Campo | Tipo   | Default            | Exemplo                | Quando mexer                                |
|-------|--------|--------------------|------------------------|---------------------------------------------|
| type  | const  | (obrigatório)      | `"sqlite"`             | —                                           |
| path  | path   | `./data/hara.db`   | `./data/safras.db`     | Múltiplos databases / share file.           |

```toml
[connectors.sql]
type = "sqlite"
path = "./data/hara.db"
```

### `type = "postgres"`

| Campo | Tipo   | Default       | Exemplo                                    | Quando mexer                                           |
|-------|--------|---------------|--------------------------------------------|--------------------------------------------------------|
| type  | const  | (obrigatório) | `"postgres"`                               | —                                                      |
| url   | secret | (obrigatório) | `"postgresql://user:pass@localhost/hara"`  | Sempre. Use `HARA_SQL_URL` no `.env` em vez de inline. |

```toml
[connectors.sql]
type = "postgres"
# url vem do env var HARA_SQL_URL — nunca commit:
```

Requer `pip install hara[postgres]`.

### `type = "memory"`

Roda em RAM (no mesmo SQLite `:memory:`). Útil **somente** para testes
single-process. **Perde estado ao sair do processo.** Para a CLI, que
faz multi-comando (ingest → semantic-map → chat), use sqlite.

```toml
[connectors.sql]
type = "memory"
```

---

## `[connectors.vector]`

Vector store para chunks de texto.

### `type = "chromadb"` (recomendado)

| Campo              | Tipo   | Default           | Exemplo            | Quando mexer                                                  |
|--------------------|--------|-------------------|--------------------|---------------------------------------------------------------|
| type               | const  | (obrigatório)     | `"chromadb"`       | —                                                             |
| persist_directory  | path   | `./data/chroma`   | `./var/chroma`     | Mover persistência para outro disco / volume Docker.          |

```toml
[connectors.vector]
type = "chromadb"
persist_directory = "./data/chroma"
```

Requer `pip install hara[chromadb]`.

### `type = "memory"` — pegadinha

```toml
[connectors.vector]
type = "memory"
```

> **Atenção**: in-process, **NÃO persiste entre invocações**. `hara
> ingest` indexa, `hara semantic-map` sobe processo novo e encontra o
> store vazio → `unstructured.yaml` fica vazio. Use só para testes
> single-process via API Python, **NUNCA via CLI multi-comando**. Para
> rodar a CLI sem depender do extra chromadb, ainda assim use
> `sqlite` no SQL e desligue `[semantic_map].regenerate_on_ingest`.

---

## `[connectors.session_store]`

Persistência de threads + turns + eventos SSE. Por default, herda do
`[connectors.sql]` — mesma URL, schema isolado.

| Campo     | Tipo    | Default              | Exemplo                       | Quando mexer                                                  |
|-----------|---------|----------------------|-------------------------------|---------------------------------------------------------------|
| ttl_days  | int     | `7`                  | `30`                          | Manter histórico mais tempo (ou menos, por privacy).          |
| type      | string  | (herda de sql)       | `"sqlite"`                    | Separar sessões em DB próprio.                                |
| path/url  | path/secret| (herda)           | `./data/sessions.db`          | Acompanha `type`.                                             |

```toml
[connectors.session_store]
ttl_days = 7
# para separar do connector SQL principal:
# type = "sqlite"
# path = "./data/sessions.db"
```

A limpeza de TTL roda no startup da API e é idempotente.

---

## `[paths]`

| Campo     | Tipo | Default     | Exemplo      | Quando mexer                                          |
|-----------|------|-------------|--------------|-------------------------------------------------------|
| data_dir  | path | `./data`    | `./var/data` | Lugar onde `hara semantic-map` escreve `*.yaml`.      |

Não cobre paths de connectors (esses ficam em `[connectors.*]`); aqui
é só para artefatos não-connector (semantic maps).

---

## `[verifier]`

| Campo | Tipo                | Default  | Exemplo    | Quando mexer                                                          |
|-------|---------------------|----------|------------|-----------------------------------------------------------------------|
| mode  | `"off"` \| `"signal"`| `"off"` | `"signal"` | Ligar análise post-hoc da resposta (sinal, sem bloquear).             |

- `off` (default): mais rápido, menor custo.
- `signal`: roda 1× ao final, popula `metadata.verifier` com:
  ```json
  {"overall_pass": false, "pct_supported": 0.62,
   "n_missing_aspects": 2, "weak_sentences": [...]}
  ```
  **Não bloqueia** a resposta. Custo: +1 LLM call (modelo hard) por
  turno.

---

## `[agent]`

Comportamento e limites do pipeline.

| Campo                 | Tipo   | Default                | Exemplo            | Quando mexer                                                                                    |
|-----------------------|--------|------------------------|--------------------|-------------------------------------------------------------------------------------------------|
| style                 | string | (texto pt-BR direto)   | (livre)            | Mudar tom de voz / persona / idioma do Synthesizer.                                             |
| language              | string | `"pt-BR"`              | `"en-US"`          | Trocar idioma. Synthesizer recebe esse hint.                                                    |
| max_history_turns     | int    | `5`                    | `10`               | Quanto histórico o Planner enxerga.                                                             |
| turn_timeout_seconds  | int    | `120`                  | `60`               | Cap end-to-end de um turno. Timeout → status `failed`.                                          |
| llm_timeout_seconds   | int    | `60`                   | `120`              | Cap por LLM call. Aumentar se usa modelos locais lentos.                                        |
| sql_max_retries       | int    | `3`                    | `5`                | Retry loop do SQL Executor quando o connector retorna erro.                                     |
| sql_max_rows          | int    | `100`                  | `500`              | LIMIT injetado quando o LLM omite. LIMIT existente acima desse valor é **clampado**.            |
| text_search_k         | int    | `5`                    | `10`               | top-K na similarity_search por sub-query text.                                                  |

```toml
[agent]
style = "Responda em inglês, formal."
language = "en-US"
max_history_turns = 10
sql_max_rows = 500
text_search_k = 10
```

---

## `[ingest]`

| Campo          | Tipo | Default | Exemplo | Quando mexer                                                    |
|----------------|------|---------|---------|-----------------------------------------------------------------|
| chunk_size     | int  | `1500`  | `1000`  | Documentos curtos / técnicos (manuais, contratos).              |
| chunk_overlap  | int  | `200`   | `100`   | Reduzir custo de embeddings em troca de menos contexto cruzado. |

`chunk_size` é em caracteres (não tokens) — proxy estável entre línguas.

---

## `[semantic_map]`

| Campo                  | Tipo | Default  | Exemplo  | Quando mexer                                                          |
|------------------------|------|----------|----------|-----------------------------------------------------------------------|
| regenerate_on_ingest   | bool | `true`   | `false`  | Pipelines onde você customiza descrições manualmente entre ingests.   |

---

## `[logging]`

| Campo  | Tipo                                              | Default     | Exemplo   | Quando mexer                                                |
|--------|---------------------------------------------------|-------------|-----------|-------------------------------------------------------------|
| level  | `"DEBUG"` \| `"INFO"` \| `"WARNING"` \| `"ERROR"` | `"INFO"`    | `"DEBUG"` | Investigando bugs do pipeline.                              |
| format | `"pretty"` \| `"json"`                            | `"pretty"`  | `"json"`  | Pipelines de log estruturado (Loki/Datadog/CloudWatch).     |

---

## Override por variáveis de ambiente

HARA segue o [12-factor app](https://12factor.net/config). Qualquer
campo do TOML pode ser sobrescrito por env var. A regra de mapeamento
é simples:

- Maiúsculas + underscore separando bloco e campo
- Aninhamento via `__` (dois underscores)

Exemplos:

| TOML                                | Env var                                     |
|-------------------------------------|---------------------------------------------|
| `[auth].token`                      | `AUTH__TOKEN`                               |
| `[api].max_message_length`          | `API__MAX_MESSAGE_LENGTH`                   |
| `[models].hard.model`               | `MODELS__HARD__MODEL`                       |
| `[connectors.sql].path`             | `CONNECTORS__SQL__PATH`                     |
| `[agent].sql_max_rows`              | `AGENT__SQL_MAX_ROWS`                       |
| `[logging].level`                   | `LOGGING__LEVEL`                            |

```bash
# trocar modelo só nessa execução, sem mexer no TOML:
MODELS__HARD__MODEL=gpt-5-mini hara chat "teste"
```

### Aliases amigáveis

Quatro env vars têm alias plano (mais legível em `.env`):

| Alias plano             | Forma nested equivalente               |
|-------------------------|----------------------------------------|
| `HARA_API_TOKEN`        | `AUTH__TOKEN`                          |
| `HARA_SQL_URL`          | `CONNECTORS__SQL__URL`                 |
| `HARA_VECTOR_URL`       | `CONNECTORS__VECTOR__URL`              |
| `HARA_SESSION_URL`      | `CONNECTORS__SESSION_STORE__URL`       |

Os aliases são bridgeados para a forma nested no startup. Use o que
for mais legível para você.

### Chaves de provider

API keys de provider seguem **convenção do provider** — não passam por
Settings. São lidas direto pelos clients (`langchain-openai`,
`anthropic`, etc.):

```dotenv
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
OPENROUTER_API_KEY=...
```

Veja `.env.example` para o template completo.

---

## Validação

`hara doctor` checa o TOML carregado e reporta cada campo. Erros de
schema (campo obrigatório faltando, tipo errado) explodem na hora do
`load_settings()` com uma mensagem do Pydantic apontando o caminho
exato.

---

## Próximos passos

- [API reference](api.md)
- [Connectors: estendendo](extending/connectors.md)
- [Getting started](getting-started.md)
