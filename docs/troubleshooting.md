# Troubleshooting

> Got an issue não listado aqui? [Abra uma issue ou discussion](https://github.com/hara-ai/hara/issues).

---

## Instalação / Setup

### 1. `hara: command not found`

**Causa:** O pacote não foi instalado no ambiente ativo, ou o virtualenv não foi ativado.

**Fix:**
```bash
pip install hara[all]
# ou, se usar uv:
uv add hara[all]
```
Verifique se o venv está ativo: `which hara` deve apontar pro diretório do projeto.

---

### 2. `ImportError: No module named 'fastapi'`

**Causa:** Instalação feita com `pip install hara` (sem extras), que não inclui o grupo `[api]`.

**Fix:**
```bash
pip install hara[api]
# ou instale tudo de uma vez:
pip install hara[all]
```

---

### 3. `ImportError: No module named 'docling'`

**Causa:** Extra `[docling]` não instalado. Necessário apenas para ingest de PDF.

**Fix:**
```bash
pip install hara[docling]
```
CSV, MD, TXT e DOCX não dependem de docling.

---

### 4. `ImportError: No module named 'chromadb'`

**Causa:** Extra `[chromadb]` não instalado.

**Fix:**
```bash
pip install hara[chromadb]
```
Para desenvolvimento local sem necessidade de persistência, use `type = "memory"` em `hara.toml`:
```toml
[connectors.vector]
type = "memory"
```

---

### 5. Erro de instalação com `uv` — Python incompatível

**Causa:** Python < 3.11 detectado. HARA requer Python 3.11+.

**Fix:**
```bash
uv python install 3.11
uv venv --python 3.11
uv add hara[all]
```

---

## Configuração / hara.toml

### 6. `pydantic.ValidationError: 3 validation errors for Settings — models, embeddings, connectors required`

**Causa:** `hara.toml` não encontrado ou está vazio. Ocorre quando `--config` aponta para caminho incorreto, ou quando `hara` é executado fora do diretório que contém o arquivo de configuração.

**Fix:**
```bash
# Gerar config inicial:
hara init

# Ou apontar explicitamente:
hara --config /caminho/absoluto/hara.toml serve

# Ou navegar ao diretório correto:
cd /meu/projeto && hara serve
```

---

### 7. `KeyError: 'OPENAI_API_KEY'` (ou outro provider)

**Causa:** Arquivo `.env` não carregado, ou a variável não está exportada no shell atual.

**Fix:**
```bash
export OPENAI_API_KEY=sk-...
# ou adicione ao .env no diretório do projeto
```
Execute `hara doctor` para diagnóstico automático de variáveis de ambiente.

---

### 8. `[auth].token is empty; refusing to serve`

**Causa:** Campo `token` em `[auth]` está vazio no `hara.toml`.

**Fix:**
```toml
[auth]
token = "meu-token-secreto"
```
Ou via variável de ambiente (sem alterar o arquivo):
```bash
export HARA_API_TOKEN=meu-token-secreto
```

---

## Conectividade / Runtime

### 9. `sqlite3.OperationalError: unable to open database file`

**Causa:** O diretório pai do arquivo `.db` não existe, ou o filesystem é somente leitura.

**Fix:**

Verifique `[connectors.sql].path` e `[connectors.session_store].path` no `hara.toml` e garanta que o diretório existe e tem permissão de escrita:
```bash
mkdir -p ./data
chmod u+w ./data
```

---

### 10. `asyncpg.InvalidPasswordError` ou `connection refused` (PostgreSQL)

**Causa:** URL de conexão incorreta ou PostgreSQL não está em execução.

**Fix:**
```bash
# Teste a conexão diretamente:
psql "postgresql://user:pass@host:5432/dbname"
```
Corrija `[connectors.sql].url` no `hara.toml` conforme o resultado.

---

### 11. `Chroma collection not found`

**Causa:** Vector store em modo `memory` perdeu estado entre duas invocações separadas da CLI (ex.: `hara ingest` seguido de `hara semantic-map` em processos distintos).

**Fix:**

Troque para `chromadb` com `persist_directory`:
```toml
[connectors.vector]
type = "chromadb"
persist_directory = "./data/chroma"
```
Veja também `docs/configuration.md`.

---

### 12. `hara doctor` retorna FAIL em provider

**Causa:** API key inválida, expirada ou rate limit atingido.

**Fix:**
```bash
hara doctor --config hara.toml
```
Execute em um shell limpo (sem variáveis residuais de outras sessões) para isolar a causa. Não exponha a key em logs ou issues públicas.

---

## Específicos de LLM

### 13. Gemini — `'list' object has no attribute 'strip'`

**Causa:** Algumas versões da API Gemini retornam o campo `content` como lista em vez de string. Known issue, corrigido via helper `extract_text` em v0.1.

**Fix:**
```bash
hara version  # deve ser >= 0.1.0
pip install --upgrade hara[all]
```

---

### 14. OpenAI gpt-4o-mini retorna SQL com markdown fences (` ```sql ` )

**Causa:** Modelo inclui cercas de código na resposta. O parser `strip_sql_fences` trata esse caso em v0.1.

**Fix:** Se o SQL bruto ainda aparecer no campo `answer`, é regressão — reporte com o payload completo da resposta.

---

### 15. `structured-output not supported for X` (Ollama, alguns modelos OpenRouter)

**Causa:** Comportamento esperado para modelos sem suporte nativo a structured output. HARA usa fallback de prompt-only JSON automaticamente.

**Fix:** Sem ação necessária para uso geral. Para structured output nativo, configure um modelo compatível (OpenAI, Anthropic, Google) em `[models]`.

---

## Ingest / Semantic-Map

### 16. `hara semantic-map` gera `unstructured.yaml` vazio

**Causa:** Vector store em modo `memory` não persiste entre `hara ingest` e `hara semantic-map`.

**Fix:** Troque para `chromadb` com `persist_directory` (veja issue #11 acima). Detalhes em `docs/configuration.md`.

---

### 17. `unable to upsert metadata: empty list value` (Chroma)

**Causa:** Conector vector recebeu `metadata.heading_path: []`. Corrigido em v0.1 — chunker agora emite `metadata.section: ""`.

**Fix:**
```bash
hara version  # confirme >= 0.1.0
pip install --upgrade hara[all]
```

---

### 18. PDF ingest muito lento + warnings de OCR

**Causa:** Docling ativa OCR por padrão, mesmo em PDFs com texto selecionável.

**Fix:** Para validar o pipeline rapidamente, use CSVs primeiro. Para PDFs, a lentidão na primeira run é esperada. Não há configuração de disable-OCR na v0.1 — será exposta em versão futura.

---

### 19. `File ingested 0 chunks`

**Causa:** PDF escaneado sem camada de texto extraível, ou arquivo Markdown sem corpo de conteúdo.

**Fix:**
```bash
# Verifique se o PDF tem texto:
pdftotext arquivo.pdf -
```
Se a saída for vazia, o arquivo precisa de OCR prévio (ex.: `ocrmypdf`) antes do ingest.

---

## API / Serve

### 20. `hara serve` warning `--workers=N → forcing 1`

**Causa:** Comportamento esperado, definido na spec §6. Múltiplos workers não são suportados em v0.1.

---

### 21. `401 INVALID_TOKEN`

**Causa:** Header `Authorization` ausente ou token incorreto.

**Fix:**
```bash
curl -H "Authorization: Bearer meu-token-secreto" http://localhost:8000/...
```
Confirme que `[auth].token` no `hara.toml` é idêntico ao valor enviado.

---

### 22. `/openapi.json` retorna `401`

**Causa:** `[api].open_docs = false` está configurado.

**Fix:**
```toml
[api]
open_docs = true   # desenvolvimento — desative em produção
```

---

### 23. SSE streaming "travado" no curl

**Causa:** curl faz buffer da saída por padrão.

**Fix:**
```bash
curl -N http://localhost:8000/threads/abc/stream
```
A flag `-N` desabilita o buffering.

---

### 24. `POST /threads/{id}/messages` retorna `425` indefinidamente

**Causa:** Orchestrator falhou silenciosamente; runner marcou o turno como `failed`.

**Fix:**
```bash
# Verifique o status do turno:
GET /threads/{id}/turns/{turn_id}
# status: "failed" confirma a falha

# Diagnostique a causa raiz:
hara doctor
```
Corrigido em v0.1 — status `failed` agora é exposto corretamente no endpoint de turno.

---

### 25. `max_message_length` não respeitado

**Causa:** Hardcoded em versões pré-v0.1.

**Fix:**
```bash
hara version  # confirme >= 0.1.0
```
Configure em `hara.toml`:
```toml
[api]
max_message_length = 4096
```

---

### 26. CORS bloqueado no browser

**Causa:** `[api].cors.allowed_origins` está vazio por padrão.

**Fix:**
```toml
[api.cors]
allowed_origins = ["http://localhost:3000"]
```

---

## Performance / Custo

### 27. Latência alta em `/invoke`

**Causa:** Esperado. O pipeline completo de agente (planner → executor → verifier) leva tipicamente 5–15s.

**Fix:** Use `/stream` para visualizar tokens conforme chegam sem esperar a resposta completa.

---

### 28. Custo de tokens alto

**Causa:** O sinal do Verifier adiciona uma chamada LLM extra com modelo hard.

**Fix:** Para runs experimentais, desative:
```toml
[verifier]
mode = "off"
```
Monitore `metadata.tokens` no response para contabilização precisa.

---

### 29. OOM durante ingest de PDF grande

**Causa:** Docling carrega o documento inteiro em memória.

**Fix:** Divida o PDF antes do ingest:
```bash
pdftk input.pdf burst output chunk_%04d.pdf
```
Ou reduza `chunk_size` na configuração do conector de ingest.

---

## Dicas de debugging

### 30. Verbose logs

Ative em `hara.toml` para ver prompts injetados nos LLMs e respostas brutas:
```toml
[logging]
level = "DEBUG"
```

---

### 31. Inspecionar SessionStore

```bash
sqlite3 ./data/session.db ".tables"
sqlite3 ./data/session.db "SELECT * FROM hara_turns LIMIT 5;"
```

---

### 32. Replay de um turno

Use o endpoint de eventos para inspecionar todos os eventos persistidos de um turno específico:
```
GET /threads/{thread_id}/turns/{turn_id}/events
```
