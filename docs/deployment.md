# Guia de deployment

Checklist e guia de hardening para quem vai expor `hara serve` em rede. Foco em operações: reverse proxy, process management, backups e troubleshooting de produção.

> Referências cruzadas: campos TOML em [docs/configuration.md](configuration.md).

## Índice

1. [Checklist pre-flight](#1-checklist-pre-flight)
2. [Hardening de auth](#2-hardening-de-auth)
3. [Reverse proxy — Nginx](#3-reverse-proxy--nginx)
4. [Gerenciamento de processo — systemd](#4-gerenciamento-de-processo--systemd)
5. [Backups de banco de dados](#5-backups-de-banco-de-dados)
6. [Observabilidade](#6-observabilidade)
7. [Dimensionamento de recursos](#7-dimensionamento-de-recursos)
8. [Monitoramento de custo](#8-monitoramento-de-custo)
9. [Multi-tenancy](#9-multi-tenancy)
10. [Disaster recovery](#10-disaster-recovery)
11. [Problemas comuns em produção](#11-problemas-comuns-em-produção)
12. [Caminho de upgrade](#12-caminho-de-upgrade)

---

## 1. Checklist pre-flight

Execute antes de subir em produção.

- [ ] `hara doctor` retorna OK em todos os checks
- [ ] `[auth].token` é forte — gere com:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- [ ] `[api].open_docs = false` (recomendado para rede pública)
- [ ] `[api.cors].allowed_origins` configurado explicitamente — **não use `["*"]` em produção**
- [ ] `[connectors.sql]` apontando para PostgreSQL (`type = "postgres"`) — não `memory` ou `sqlite`
- [ ] `[connectors.vector]` apontando para ChromaDB com `persist_directory` real — não `memory`
- [ ] `[connectors.session_store].ttl_days` ajustado conforme política de retenção (default `7`)
- [ ] `[verifier].mode` decidido — `"off"` é mais barato; `"signal"` adiciona quality metric por ~30% a mais de tokens
- [ ] `[agent].sql_max_rows` ajustado conforme carga esperada (default `100`)
- [ ] `.env` com todas as API keys presentes — **nunca commitar ao git**
- [ ] `[logging].level = "INFO"` — `DEBUG` vaza conteúdo de prompts em log

---

## 2. Hardening de auth

- Bearer token via `Authorization: Bearer <token>` é o **único** mecanismo de autenticação no v0.1.
- HARA não tem multi-user: 1 token = 1 superuser com acesso total à API.
- Se precisar de RBAC ou múltiplos usuários, coloque um reverse proxy com auth lateral (oauth2-proxy, Auth0, Cloudflare Access) na frente de HARA.
- **HARA não termina TLS diretamente.** Termine TLS no reverse proxy (Nginx, Caddy, Cloudflare Tunnel).
- **Token rotation:** altere `[auth].token` no `hara.toml`, reinicie `hara serve`. Não há zero-downtime no v0.1 — clientes recebem 401 durante o restart.

---

## 3. Reverse proxy — Nginx

Snippet completo com TLS termination e suporte a SSE (streaming).

```nginx
upstream hara {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name hara.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name hara.example.com;

    ssl_certificate     /etc/ssl/hara.crt;
    ssl_certificate_key /etc/ssl/hara.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://hara;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE: desabilita buffering para que eventos cheguem em tempo real
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 600s;
    }

    # Protege /openapi.json mesmo com open_docs = true
    location = /openapi.json {
        auth_basic "HARA Docs";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://hara;
    }
}
```

Gere o `.htpasswd`:

```bash
htpasswd -c /etc/nginx/.htpasswd ops
```

> Caddy alternative: `reverse_proxy localhost:8000` já faz TLS automático via Let's Encrypt. Adicione `flush_interval -1` no bloco `reverse_proxy` para SSE.

---

## 4. Gerenciamento de processo — systemd

Crie `/etc/systemd/system/hara.service`:

```ini
[Unit]
Description=HARA Hybrid Agent
After=network.target postgresql.service

[Service]
Type=simple
User=hara
Group=hara
WorkingDirectory=/opt/hara
EnvironmentFile=/opt/hara/.env
ExecStart=/opt/hara/.venv/bin/hara serve --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

# Limite de descritores de arquivo (para conexões simultâneas)
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Ative e acompanhe:

```bash
# Criar usuário sem login
useradd --system --no-create-home --shell /usr/sbin/nologin hara

# Copiar projeto e ajustar dono
chown -R hara:hara /opt/hara

systemctl daemon-reload
systemctl enable --now hara

# Acompanhar logs em tempo real
journalctl -u hara -f
```

---

## 5. Backups de banco de dados

HARA é stateless além de três stores — backup os três para ter recovery completo:

| Store | Tabelas / Path | Estratégia |
|---|---|---|
| PostgreSQL | `hara_threads`, `hara_turns`, `hara_events` | `pg_dump` via cron (ver abaixo) |
| SQLite (se usado) | arquivo `.db` | `VACUUM` + `cp` |
| ChromaDB | `[connectors.vector].persist_directory` | backup do diretório |

Cron diário para PostgreSQL:

```bash
# /etc/cron.d/hara-backup
0 3 * * * hara pg_dump $HARA_SQL_URL | gzip > /backups/hara_$(date +\%F).sql.gz
```

**Atomicidade:** HARA não produz snapshots transacionais entre os stores. Para evitar inconsistência, pause `hara serve` (`systemctl stop hara`) durante o backup, depois retome. Em ambientes que tolerem eventual consistency, backup online é aceitável — o risco é ter um turn `running` sem os events correspondentes no vector store.

Retenção sugerida: 30 dias em disco local + offload para object storage (S3, GCS, Backblaze B2).

---

## 6. Observabilidade

**Logs estruturados:** v0.1 usa `structlog`. Configure `[logging].format = "json"` em produção para parse por Loki, Datadog, CloudWatch, etc. Use `"pretty"` apenas em desenvolvimento.

```toml
[logging]
level  = "INFO"
format = "json"
```

**Correlação de turnos:** cada log entry de erro (`HARA_API_ERROR`, `LLM_PROVIDER_ERROR`, etc.) inclui o `turn_id`. Use esse campo para correlacionar com requests da aplicação cliente.

**Métricas por turno:** cada response inclui `metadata.tokens` (input/output/total) e `metadata.latency_per_node` (latência por nó do pipeline). Colete esses campos via middleware ou aggregator próprio. OpenTelemetry está no roadmap v0.2.

**Health check:** `GET /health` (sem auth) retorna `200 OK` quando o processo está up. `GET /doctor` (auth obrigatório) valida conectividade com todos os connectors.

---

## 7. Dimensionamento de recursos

Regras gerais para dimensionar a instância:

| Dimensão | Nota |
|---|---|
| **CPU** | Baixo — agent é I/O bound. Spike possível em queries SQL pesadas (mitigado por `sql_max_rows`). |
| **Memória** | ~200–500 MB base + ChromaDB (~50–200 MB mapeado em memória) + ~2–10 MB por turn in-flight. |
| **Concorrência** | 1 turn = 1 `asyncio.Task`. Single worker suporta 5–20 turns simultâneos (depende da latência do LLM + I/O). |
| **Gargalo dominante** | Rate limits da API do LLM. Distribua API keys / quota se precisar de mais throughput. |
| **Disco** | Session store SQLite cresce ~5–20 KB por turn (events + row). TTL cleanup automático no startup. |

`hara serve` força `--workers=1` (restrição de arquitetura do v0.1 — TurnRegistry e EventBus são process-local).

---

## 8. Monitoramento de custo

Cada response traz `metadata.tokens`:

```json
{
  "metadata": {
    "tokens": { "input": 1240, "output": 380, "total": 1620 },
    "latency_per_node": { "planner": 1.2, "sql_executor": 0.4, "synthesizer": 2.1 }
  }
}
```

Dicas para reduzir custo:

- `[verifier].mode = "off"` elimina ~30% do token spend (sem chamada extra ao modelo hard).
- `[agent].sql_max_rows` menor reduz tokens de contexto em respostas SQL-heavy.
- Modelo `soft` (default `gpt-5-mini`) é usado nos nós de menor criticidade — não substitua por `hard` sem motivo.

---

## 9. Multi-tenancy

v0.1 não tem multi-tenancy nativo. Dois patterns que funcionam hoje:

**Per-tenant deployment** (recomendado para isolamento total): 1 instância HARA por tenant, cada uma com seu próprio `hara.toml`, banco e vector store. Mais simples de operar; custo é N processos.

**Per-tenant thread tagging** (compartilhado, sem isolamento de dados): salve `tenant_id` no campo `metadata` ao criar threads; filtre em `GET /threads`. Não há row-level security — a aplicação cliente precisa enforçar o filtro. Não recomendado quando os dados de tenants são sensíveis entre si.

Tenant scoping nativo está no roadmap v0.x, sujeito a demanda.

---

## 10. Disaster recovery

- **Backup completo** = backup dos 3 stores (PostgreSQL + ChromaDB `persist_directory` + SQLite session store se separado).
- **Falha de connector em runtime:** HARA continua up mas turns falham com erro (`LLM_PROVIDER_ERROR` ou similar). Verifique via `GET /doctor`.
- **Shutdown gracioso (SIGTERM):** `lifecycle.py` cancela tasks in-flight e aguarda. Turns interrompidos ficam com `status = "failed"`.
- **SIGKILL / crash:** rows podem ficar presas em `status = "running"`. Veja workaround em [Common issues](#11-common-production-issues).

---

## 11. Problemas comuns em produção

**Turns presos em `running` após restart**

Causado por SIGKILL ou crash. Workaround manual no PostgreSQL:

```sql
UPDATE hara_turns
SET    status = 'failed'
WHERE  status = 'running'
  AND  started_at < NOW() - INTERVAL '30 minutes';
```

v0.2 introduz um reaper automático para orphaned turns.

**Esgotamento de connection pool no PostgreSQL**

HARA abre conexão por chamada no v0.1 (sem pool configurável). Se a carga for alta, aumente `max_connections` no `postgresql.conf` ou coloque PgBouncer na frente.

**ChromaDB com memory bloat**

Use `persist_directory` em disco (nunca `type = "memory"` em produção). Se vazamento aparecer, reiniciar o processo recupera a memória — os dados persistidos no diretório são preservados.

**`hara serve` recusa subir com "token vazio"**

`[auth].token` está vazio em `hara.toml` ou a variável `HARA_API_TOKEN` não está no `.env`. Gere um token e configure antes de subir.

**SSE não flui pelo proxy**

Confirme `proxy_buffering off` e `proxy_cache off` no Nginx. Em AWS ALB, verifique idle timeout (padrão 60 s é baixo para turnos longos — ajuste para 600 s).

---

## 12. Caminho de upgrade

```bash
# 1. Backup dos stores
systemctl stop hara
pg_dump $HARA_SQL_URL > /backups/hara_pre_upgrade.sql

# 2. Atualizar pacote
/opt/hara/.venv/bin/pip install --upgrade hara[all]

# 3. Checar CHANGELOG para breaking changes / migrations
# v0.2 introduz Alembic — rode `hara db migrate` quando disponível

# 4. Smoke test
hara doctor

# 5. Retomar
systemctl start hara
```

Sempre leia o [CHANGELOG](../CHANGELOG.md) antes de atualizar. Schema changes entre versões serão marcados como breaking. Backup antes de qualquer upgrade é obrigatório.
