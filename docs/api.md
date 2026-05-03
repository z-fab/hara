# Referência da API HARA

Referência completa da API HTTP exposta pelo `hara serve`. Todos os
exemplos abaixo assumem:

```bash
export BASE=http://localhost:8000
export TOKEN=...     # valor de [auth].token (ou variável de ambiente HARA_API_TOKEN)
```

A API é implementada com FastAPI; um Swagger UI interativo é servido
em `/docs` (protegido por auth quando `[api].open_docs=false`).

## Autenticação

Todas as rotas **exceto `GET /health`** exigem um token Bearer. Passe-o
no header `Authorization`:

```
Authorization: Bearer <token>
```

### Como obter um token

`hara init` gera um para você quando responde "Y" ao prompt de auth.
Programaticamente:

```python
>>> import secrets; secrets.token_urlsafe(32)
'7K2m_b3nQ-vJxLmH1pAa9qZqYw7TtRnG2v8FwQv-aNs'
```

Defina via:

```dotenv
# .env
HARA_API_TOKEN=7K2m_b3nQ-vJxLmH1pAa9qZqYw7TtRnG2v8FwQv-aNs
```

Esta variável de ambiente é mapeada automaticamente para `[auth].token`.

### Erros de autenticação

| Header / estado                            | Status | Code              |
|--------------------------------------------|--------|-------------------|
| Header ausente                             | 401    | `INVALID_TOKEN`   |
| Header malformado (sem prefixo `Bearer `)  | 401    | `INVALID_TOKEN`   |
| Token não confere com `[auth].token`       | 401    | `INVALID_TOKEN`   |

## Formato da resposta de erro

Todo erro segue o mesmo envelope JSON:

```json
{
  "error": {
    "code": "THREAD_NOT_FOUND",
    "message": "Thread thr_xyz not found",
    "details": {}
  }
}
```

O status code é definido por classe de erro. O objeto `details` é
livre e pode carregar contexto adicional (ex.: `{"sql": "...", "reason": "..."}`
para erros de validação de SQL).

---

## Endpoints

### `GET /health`

Liveness probe. **Sem auth.** Sem acesso a DB.

```bash
curl -s $BASE/health
```

```json
{ "status": "ok" }
```

| Status | Significado              |
|--------|--------------------------|
| 200    | Processo está vivo.      |

---

### `GET /version`

```bash
curl -s -H "Authorization: Bearer $TOKEN" $BASE/version
```

```json
{ "version": "0.1.0" }
```

| Status | Code               |
|--------|--------------------|
| 200    | —                  |
| 401    | `INVALID_TOKEN`    |

---

### `POST /threads`

Cria uma nova thread (conversa).

**Request body**:

```json
{
  "title": "Análise safra 2023",     // opcional
  "metadata": {"project": "abc"}     // opcional, livre
}
```

```bash
curl -s -X POST $BASE/threads \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Análise safra 2023"}'
```

**Response 201**:

```json
{
  "thread_id": "thr_8f3a2c",
  "created_at": 1714658400.123
}
```

| Status | Code               |
|--------|--------------------|
| 201    | —                  |
| 401    | `INVALID_TOKEN`    |
| 422    | (erro de validação)|

---

### `GET /threads`

Lista threads recentes. Aceita `?limit=N` opcional (default 50).

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/threads?limit=10"
```

**Response 200**:

```json
[
  {
    "thread_id": "thr_8f3a2c",
    "title": "Análise safra 2023",
    "created_at": 1714658400.123,
    "last_active_at": 1714658405.789
  }
]
```

---

### `GET /threads/{thread_id}`

Busca uma única thread.

```bash
curl -s -H "Authorization: Bearer $TOKEN" $BASE/threads/thr_8f3a2c
```

**Response 200**: mesmo formato do item da listagem.

| Status | Code                  |
|--------|-----------------------|
| 200    | —                     |
| 404    | `THREAD_NOT_FOUND`    |

---

### `DELETE /threads/{thread_id}`

Cascade delete: remove a thread, todos os seus turns e todos os eventos SSE.

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" $BASE/threads/thr_8f3a2c
```

| Status | Code                  |
|--------|-----------------------|
| 204    | (sem conteúdo)        |
| 404    | `THREAD_NOT_FOUND`    |

---

### `POST /threads/{thread_id}/messages`

Inicia um novo turn de forma assíncrona. Retorna imediatamente com o
`turn_id`; o agente roda em background. Inscreva-se nos eventos via
`GET /threads/{id}/turns/{id}/events` para receber tokens.

**Request body**:

```json
{ "message": "Qual a produção de soja em MT em 2023?" }
```

Restrições: `1 ≤ len(message) ≤ [api].max_message_length` (default
8000).

```bash
curl -s -X POST $BASE/threads/thr_8f3a2c/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"Qual a produção de soja?"}'
```

**Response 202**:

```json
{
  "turn_id": "trn_4a1b2c3d",
  "thread_id": "thr_8f3a2c",
  "status": "running",
  "started_at": 1714658410.456
}
```

| Status | Code                  |
|--------|-----------------------|
| 202    | —                     |
| 401    | `INVALID_TOKEN`       |
| 404    | `THREAD_NOT_FOUND`    |
| 422    | (validação: mensagem vazia / longa demais) |

---

### `GET /threads/{thread_id}/turns`

Lista turns recentes de uma thread. Aceita `?limit=N` opcional (default 20).

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/threads/thr_8f3a2c/turns?limit=5"
```

**Response 200**: array de `TurnOut` (mesmo formato da resposta de
turn único abaixo).

| Status | Code                  |
|--------|-----------------------|
| 200    | —                     |
| 404    | `THREAD_NOT_FOUND`    |

---

### `GET /threads/{thread_id}/turns/{turn_id}`

Busca o resultado final de um turn. **Retorna 425 se ainda em execução.**

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  $BASE/threads/thr_8f3a2c/turns/trn_4a1b2c3d
```

**Response 200**:

```json
{
  "turn_id": "trn_4a1b2c3d",
  "thread_id": "thr_8f3a2c",
  "status": "completed",
  "question": "Qual a produção de soja?",
  "answer": "A produção de soja em 2023 lidera com Mato Grosso (42,5 Mt) <ref:1>...",
  "citations": [
    {
      "evidence_id": 1,
      "kind": "sql",
      "source": "conab_safras",
      "section": "",
      "snippet": "SELECT uf, producao_t FROM conab_safras..."
    }
  ],
  "metadata": {
    "routing": {"subqueries_count": 1, "routes_taken": ["sql"]}
  },
  "started_at": 1714658410.456,
  "completed_at": 1714658415.789,
  "canceled_at": null
}
```

`status` é um entre `running | completed | failed | canceled`.

| Status | Code                  | Notas                                         |
|--------|-----------------------|-----------------------------------------------|
| 200    | —                     | Turn concluído (ou failed/canceled).          |
| 404    | `TURN_NOT_FOUND`      | turn_id desconhecido.                         |
| 425    | `TURN_NOT_READY`      | Turn ainda em execução; inscreva-se nos eventos. |

> A resposta 425 é o sinal canônico para um cliente de polling indicando
> que ele deve trocar para SSE (ou recuar e tentar novamente).

---

### `GET /threads/{thread_id}/turns/{turn_id}/events`

**Stream de Server-Sent Events** para um turn. Reproduz eventos passados
do log persistido primeiro, depois acompanha novos eventos a partir de
uma fila em memória. Envia um keepalive `: ping` a cada 15 segundos.

```bash
curl -N --no-buffer \
  -H "Authorization: Bearer $TOKEN" \
  $BASE/threads/thr_8f3a2c/turns/trn_4a1b2c3d/events
```

> **Sempre passe `--no-buffer`** para o curl em um terminal — sem ele,
> o curl segura o body inteiro até EOF e você não vê nada ao vivo.

**Tipos de eventos**:

| event   | formato do data                                           | Emitido em                                  |
|---------|-----------------------------------------------------------|---------------------------------------------|
| `state` | `{"node": "planner", "status": "start"}`                  | Início/fim de cada node do pipeline.        |
| `state` | `{"node": "sql_executor", "status": "end", "rows": 4}`    | (campos extras por node)                    |
| `token` | `{"text": "A produção"}`                                  | Por token do LLM no Synthesizer.            |
| `final` | `{"answer": "...", "citations": [...], "metadata": {...}}`| Uma única vez no final, com o TurnResult completo. |
| `error` | `{"code": "LLM_PROVIDER_ERROR", "message": "..."}`        | Se o turn falhar.                           |

Formato wire:

```
event: state
data: {"node":"planner","status":"start"}

event: token
data: {"text":"A produção"}

event: final
data: {"answer":"...","citations":[...]}

```

#### Exemplo de cliente JS

```js
const url = `${BASE}/threads/${threadId}/turns/${turnId}/events`;
// EventSource não suporta headers customizados — você vai precisar de um polyfill
// ou auth via cookie no servidor para uso em browser. A lib eventsource do Node suporta:
import EventSource from 'eventsource';
const es = new EventSource(url, {
  headers: { Authorization: `Bearer ${TOKEN}` },
});

es.addEventListener('state', (ev) => {
  console.log('state:', JSON.parse(ev.data));
});
es.addEventListener('token', (ev) => {
  process.stdout.write(JSON.parse(ev.data).text);
});
es.addEventListener('final', (ev) => {
  console.log('\nfinal:', JSON.parse(ev.data));
  es.close();
});
es.addEventListener('error', (ev) => {
  console.error('error:', ev);
  es.close();
});
```

#### Exemplo de cliente Python (httpx)

```python
import httpx, json

with httpx.stream(
    "GET",
    f"{BASE}/threads/{thread_id}/turns/{turn_id}/events",
    headers={"Authorization": f"Bearer {TOKEN}"},
    timeout=None,
) as r:
    event = None
    for line in r.iter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            payload = json.loads(line[6:])
            if event == "token":
                print(payload["text"], end="", flush=True)
            elif event == "final":
                print("\n", payload)
                break
```

| Status | Code                  |
|--------|-----------------------|
| 200    | —                     |
| 404    | `TURN_NOT_FOUND`      |

---

### `DELETE /threads/{thread_id}/turns/{turn_id}`

Cancela um turn em execução. **Idempotente** — retorna 204 mesmo para
turns já completos/cancelados/em estado desconhecido. A task asyncio é
cancelada; clientes no stream SSE veem um evento de estado final `canceled`.

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  $BASE/threads/thr_8f3a2c/turns/trn_4a1b2c3d
```

| Status | Code                  |
|--------|-----------------------|
| 204    | (sem conteúdo)        |
| 404    | `TURN_NOT_FOUND`      |

---

### `POST /invoke`

Wrapper síncrono de conveniência. Cria uma thread se `thread_id` for
omitido, executa um turn, aguarda a conclusão e retorna o resultado
completo. Equivalente a `POST /threads/{}/messages` + polling em
`/turns/{}` até finalizar — mas em uma única requisição.

**Request body**:

```json
{
  "thread_id": "thr_8f3a2c",   // opcional; cria automaticamente se omitido
  "message": "Qual a produção de soja?"
}
```

```bash
curl -s -X POST $BASE/invoke \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"Qual a produção de soja?"}'
```

**Response 200**: mesmo formato do evento SSE `final`:

```json
{
  "turn_id": "trn_4a1b2c3d",
  "thread_id": "thr_e5f6g7h8",
  "answer": "A produção...",
  "citations": [...],
  "metadata": {...}
}
```

| Status | Code                  |
|--------|-----------------------|
| 200    | —                     |
| 401    | `INVALID_TOKEN`       |
| 422    | (validação)           |
| 502    | `LLM_PROVIDER_ERROR` / `CONNECTOR_ERROR` / `SQL_VALIDATION_ERROR` |
| 500    | `INTERNAL_ERROR` / `INVALID_CONFIG` |

> Chamadas longas bloqueiam a conexão — um único `/invoke` pode
> levar 30–120s em turns complexos. Use `/stream` (SSE) quando a
> latência importar, ou o fluxo assíncrono `messages` + `events`
> quando você também quiser conexões retomáveis.

---

### `POST /stream`

Igual ao `/invoke`, mas retorna SSE em vez de esperar. Use como
alternativa de streaming em requisição única quando você não precisar
de URLs de turn persistentes desde o início.

**Request body**: idêntico ao `/invoke`.

```bash
curl -N --no-buffer -X POST $BASE/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"Qual a produção de soja?"}'
```

**Response**: stream SSE com os mesmos tipos de eventos de
`/threads/{}/turns/{}/events`. O payload do evento `final` inclui o
`turn_id` + `thread_id` para que o cliente possa persistí-los depois.

| Status | Code                  |
|--------|-----------------------|
| 200    | —                     |
| 401    | `INVALID_TOKEN`       |
| 422    | (validação)           |

---

### `GET /doctor`

Versão JSON do CLI `hara doctor`. Mesmas checagens; útil para
dashboards de saúde e automação de ops.

```bash
curl -s -H "Authorization: Bearer $TOKEN" $BASE/doctor
```

**Response 200** (todas as checagens passando):

```json
{
  "status": "ok",
  "checks": [
    {"check": "auth.token", "status": "OK", "detail": "token set"},
    {"check": "provider.openai", "status": "OK", "detail": "OPENAI_API_KEY ok"},
    {"check": "connector.sql (sqlite)", "status": "OK", "detail": "sqlite ready"}
  ]
}
```

**Response 503** (qualquer checagem falhando):

```json
{
  "status": "degraded",
  "checks": [
    {"check": "provider.openai", "status": "FAIL", "detail": "OPENAI_API_KEY not set"}
  ]
}
```

| Status | Notas                                                |
|--------|------------------------------------------------------|
| 200    | Todas as checagens `OK`.                             |
| 503    | Pelo menos uma `FAIL`. O body ainda traz os detalhes. |

---

## Códigos de erro

Definidos em `hara.api.errors`:

| Code                     | HTTP | Quando                                                            |
|--------------------------|------|-------------------------------------------------------------------|
| `INVALID_TOKEN`          | 401  | Token Bearer ausente/malformado/incorreto.                        |
| `THREAD_NOT_FOUND`       | 404  | thread_id não existe.                                             |
| `TURN_NOT_FOUND`         | 404  | turn_id não existe (sob a thread informada).                      |
| `TURN_NOT_READY`         | 425  | `GET turn` enquanto o turn ainda está `running`.                  |
| `LLM_PROVIDER_ERROR`     | 502  | Chamada upstream ao LLM falhou (timeout, 5xx, rate limit).        |
| `CONNECTOR_ERROR`        | 502  | Connector SQL ou vetorial levantou exceção durante o turn.        |
| `SQL_VALIDATION_ERROR`   | 502  | SQL gerado falhou na validação do sqlglot (write op, função perigosa). |
| `INVALID_CONFIG`         | 500  | Config inválida detectada em runtime (raro — quase tudo é pego no startup). |
| `INTERNAL_ERROR`         | 500  | Catch-all de exceções inesperadas.                                |

---

## Limites & timeouts

Defaults do `hara.toml` (blocos `[api]` e `[agent]`):

| Configuração                          | Default | Fonte                       |
|---------------------------------------|---------|-----------------------------|
| Tamanho máximo da mensagem (chars)    | 8000    | `[api].max_message_length`  |
| Snippet de citação (chars)            | 200     | `[api].snippet_max_chars`   |
| Timeout end-to-end do turn (s)        | 120     | `[agent].turn_timeout_seconds` |
| Timeout de chamada única ao LLM (s)   | 60      | `[agent].llm_timeout_seconds`  |
| Retries de SQL por chamada de node    | 3       | `[agent].sql_max_retries`   |
| Cap de linhas SQL (clamp / inject LIMIT) | 100  | `[agent].sql_max_rows`      |
| Top-K do retriever de texto           | 5       | `[agent].text_search_k`     |
| TTL de sessão (dias)                  | 7       | `[connectors.session_store].ttl_days` |
| Keepalive SSE (s)                     | 15      | (hardcoded em `sse.py`)     |

A frequência do keepalive não é configurável pelo usuário no v0.1;
se um proxy na frente da API tiver um idle timeout mais apertado,
diminua-o via config do reverse-proxy ou rode o uvicorn atrás de outro.

---

## CORS

O middleware de CORS é **montado apenas quando** `[api.cors].allowed_origins`
não está vazio. Comportamento quando configurado:

- `Access-Control-Allow-Origin`: confere com a lista configurada
- `Access-Control-Allow-Credentials`: `true`
- `Access-Control-Allow-Methods`: `*`
- `Access-Control-Allow-Headers`: `*`

Requisições pre-flight `OPTIONS` retornam 204 + os headers acima.
Para requisições com credentials, browsers exigem uma origem explícita
(sem wildcards) — liste cada origem que você suporta:

```toml
[api.cors]
allowed_origins = [
  "https://app.exemplo.com.br",
  "http://localhost:3000",
]
```

---

## OpenAPI / Swagger

Quando `[api].open_docs = true` (default):

- `GET /docs` → Swagger UI interativo (sem auth)
- `GET /redoc` → Redoc UI (sem auth)
- `GET /openapi.json` → spec OpenAPI 3 (sem auth)

Quando `false` (recomendado para deploys públicos):

- As três URLs exigem `Authorization: Bearer ...`.
- O schema ainda é servido (apenas protegido). Consuma a partir de
  ferramentas internas via requisições assinadas.

---

## Compatibilidade & versionamento

A v0.1 é alpha. Mudanças quebradoras entre versões minor são possíveis.
O enum `error.code` é estável — uma vez lançados, os codes não serão
renomeados (apenas adicionados). Campos do schema podem crescer, mas
não encolhem.

Para o changelog, veja [`CHANGELOG.md`](../CHANGELOG.md).
