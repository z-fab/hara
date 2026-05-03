# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-03 — Plano 4: API + Init + Docs (v0.1 release)

### Added (esta release)

- **FastAPI HTTP layer** (spec §6) — full event-sourced threads/turns + SSE streaming + Bearer auth + CORS + OpenAPI docs gating. Endpoints:
  - `GET /health` (no auth) + `GET /version`
  - Threads: `POST/GET/GET-detail/DELETE /threads`
  - Turns: `POST /threads/{id}/messages` (202 async), `GET /threads/{id}/turns`, `GET /threads/{id}/turns/{id}` (425 polling), `GET /threads/{id}/turns/{id}/events` (SSE replay+tail), `DELETE /threads/{id}/turns/{id}` (cancel)
  - Convenience: `POST /invoke` (sync) + `POST /stream` (SSE) + `GET /doctor`
- **`hara init`** — 9-prompt Rich wizard for `hara.toml` + `.env`. `--non-interactive` writes Docker/CI defaults. `--force` overwrites existing.
- **`hara serve`** — uvicorn launcher; forces `--workers=1` (spec §6 single-worker req); rejects empty `[auth].token`; lazy uvicorn import (api extra).
- **TTL cleanup** at startup (spec §17 critério 21) — drops turns older than `[connectors.session_store].ttl_days × 86400`.
- **Event-sourced infrastructure**: `TurnRegistry` (process-local turn_id → asyncio.Task), `EventBus` (per-turn pub/sub), `ApiTurnRunner` (persists then publishes; replay-then-tail invariant), Orchestrator `on_event` ContextVar with per-node state events + per-token events.
- **Documentation suite** (`docs/`): `getting-started.md`, `configuration.md`, `api.md`, `extending/connectors.md` (MySQL example for spec §17 critério 20).
- **Recommended v0.1 deployment**: host CLI (`pip install hara[all] && hara init && hara serve`). Docker support deferred to v0.2 — pre-release iterations surfaced build size pain (~40min, pulls docling/PyTorch) and bind-mount sharp edges; we'll ship a hardened lean image when the rest of v0.2 polish lands.
- **README.md** — full rewrite with quickstart + features + architecture diagram + CLI reference.

### Infraestrutura de testes

- 7 e2e tests in `tests/integration/api/` covering happy path, SSE streaming + replay, invoke/stream parity (spec §17 critério 10), cancel, TTL cleanup.
- 504 unit + integration tests pass; 1 skipped (TestClient-fragile streaming, covered by integration).
- Pyright strict 0 errors. Coverage 85% on `src/hara/` (above the 80% gate).

### Resumo da arquitetura (cumulativo)

- **Plano 1**: foundation, config (Pydantic-Settings v2 + TOML), connectors + providers (entry-point discovery).
- **Plano 2**: ingest (CSV/PDF/DOCX/MD/TXT) + semantic-map (`with_structured_output` + JSON fallback).
- **Plano 3**: agent (LangGraph multi-agent pipeline) + `hara chat`.
- **Plano 4**: API + init + docs (this release).

### Achados de review tratados nesta release

- Review #1 P2: `__version__` now reads from `importlib.metadata.version("hara")` so `/version` and OpenAPI auto-sync to pyproject.
- Review #2 P1: turn lifecycle — orchestrator accepts external `turn_id` and finalizes existing rows via new `update_turn_result` helper (no more duplicate rows).
- Review #2 P2: SSE subscribe-before-replay race fixed via seq-attached events + dedupe.
- Review #2 P2: synthesizer token tasks awaited before emitting `final`/`done` (no more out-of-order events).
- Review #3 P1: `/invoke` and `/stream` raise `INVALID_CONFIG` when orchestrator unavailable.
- Review #3 P2: `/invoke` and `/stream` validate `thread_id` existence (no orphaned rows).
- Review #3 P2: SSE handler closes immediately for terminal turns (replay-only path).
- Review #3 P3: `list_turns` now returns `canceled_at` so DELETE timestamp surfaces.

## [0.1.0a3] - 2026-05-02 — Plano 3: Agent + `hara chat`

### Added
- `hara chat "pergunta"` (one-shot) and `hara chat` (REPL) — Rich Live streaming with `<ref:N>` markers and a citations table. `--thread-id <id>` resumes a thread; `--no-stream` prints the whole answer at once. Ctrl-C cancels: exit 130 in one-shot, returns to prompt in REPL.
- LangGraph multi-agent pipeline: Planner → SQL Executor / Text Retriever → Synthesizer (streaming) → optional Verifier (signal-only). Reuse implícito when Planner returns `subqueries=[]`.
- SQL safety formal (Camada 1+2 of spec §13): sqlglot AST validation + LIMIT injection (clamps existing LIMIT > max_rows down to max_rows) + `read_only=True` enforcement on all connectors. Rejects non-SELECT, multi-statement, CTE-wrapped DML, and dialect-specific dangerous functions (`pg_read_file`, `xp_cmdshell`, `load_extension`, `load_file`, etc.).
- `<ref:N>` marker protocol — Synthesizer streams text, orchestrator extracts citations deterministically. Edge cases covered: malformed markers ignored, repeated markers dedup, unsupported markers don't crash (counted in `metadata.unsupported_markers`).
- Multi-turn via SessionStore: `record_turn` persists question/answer/citations/metadata; `list_turns` reloads N most recent. Orchestrator's `_load_history` and `_load_accumulated` (the latter via `metadata._evidence_pool`) wire prior turns into the next Planner + Synthesizer pass.
- `[verifier].mode = "signal"` populates `metadata.verifier` with `{overall_pass, pct_supported, n_missing_aspects, weak_sentences}` without blocking the answer.
- Per-node LLM resolution: `[models.{planner,sql,synthesizer,verifier}]` overrides; defaults to `hard`/`soft` tiers per spec §3.
- `MemoryVectorConnector` now honors Chroma-style `{"file_id": {"$in": [...]}}` filters (parity with the Chroma backend).
- `hara doctor` reports semantic-map presence (warn) and per-node model resolution.

### Changed
- `services/semantic_map.py` `_invoke_with_fallback` → `agent/llm_invoke.py` `invoke_with_fallback` so all agent nodes share the dual `with_structured_output + JSON fallback` pattern.
- `Orchestrator.on_token` callback wired through a module-level `ContextVar` instead of an instance slot — concurrent `run_turn` calls (Plano 4 API) no longer cross-contaminate streaming.

### Infraestrutura de testes
- `tests/integration/test_agent_e2e.py` — 5 end-to-end scenarios using `FakeChatModel` + memory connectors. No external services; runs in CI Tier 1.
- Coverage on `src/hara/agent/`: 86%. Pyright strict: 0 errors. 372 tests pass.

## [0.1.0a2] - 2026-05-02 — Plano 2: Ingest + Semantic Map

### Added
- `hara ingest --from <path>` — multimodal CSV/PDF/DOCX/MD/TXT ingestion with
  `--on-conflict={skip,replace,append}` and `--strict`.
- `hara semantic-map [--force]` — generates `data/structured.yaml` and
  `data/unstructured.yaml`. Uses centroid + similarity search for documents,
  `list_tables` + LLM for tables.
- `hara_ingested_files` operations on `SessionStore` (record/find/list/delete).
- `python-slugify` and `pyyaml` runtime deps.
- 2-stage chunker (heading + size with overlap).
- File scanner classifying by extension; deterministic ordering.
- Auto-trigger of semantic-map regeneration after ingest when
  `[semantic_map].regenerate_on_ingest = true`.

### Fixed
- (none)

### Notes
- Vector `--on-conflict=append` is rejected by design (would duplicate chunks
  silently). Use `replace` or rename the file.
- Docling extra (`pip install hara[docling]`) is required for PDF/DOCX.

## [0.1.0a1] - 2026-05-02 — Plano 1: Foundation

### Added

**Project scaffolding**
- `pyproject.toml` with hatchling build, optional extras (`api`, `postgres`, `chromadb`, `anthropic`, `google`, `docling`, `all`), entry-points for connectors and providers.
- `uv` for dependency management with `[dependency-groups] dev` for test/lint/type tooling.
- `.python-version` pinned to 3.11.
- `ruff` configured for lint + format with strict rule selection (E/W/F/I/N/UP/B/C4/SIM/RUF/S/T20/TID/PT/RET/ARG/ERA/PL).
- `pyright` configured strict on `src/hara`, basic on `tests`.
- `pytest` with `asyncio_mode = "auto"` and custom markers (`live`, `contract`, `integration`).

**Configuration**
- `Pydantic-Settings` root with `TomlConfigSettingsSource` for `hara.toml` + `.env` + env-var layering.
- Discriminated unions for SQL connectors (sqlite/postgres/memory) and Vector connectors (chromadb/memory).
- Hierarchical settings: server, auth, api (with cors), models (hard/soft + per-node overrides), embeddings, providers, connectors, verifier, agent, ingest, semantic_map, logging.
- `hara.toml.example` and `.env.example` templates.

**Logging**
- `structlog` with two output formats: pretty (Rich console) for dev, JSON line for prod.
- `configure_logging(level, format)` idempotent setup; integrates with stdlib logging.

**Session Store**
- `SQLite`-backed via `aiosqlite`; idempotent `CREATE TABLE IF NOT EXISTS` on startup.
- Schema: `hara_threads`, `hara_turns`, `hara_events`, `hara_ingested_files` with appropriate FKs and indices.
- Basic thread CRUD (`create_thread`, `get_thread`).

**Connectors layer**
- `SQLConnector` ABC + types (`SQLResult`, `TableInfo`, `ColumnInfo`, `ColumnStatistics`, `ConnectorHealth`).
- `VectorConnector` ABC + types (`TextChunk`, `DocumentInfo`).
- Plugin discovery via `importlib.metadata.entry_points` + programmatic registration (`register_*`, `resolve_*_class`, `list_*`).
- Reusable contract test mixins (`SQLConnectorContractTests`, `VectorConnectorContractTests`) and `FakeEmbedder` for third-party connector authors.

**SQL connector implementations**
- `MemorySQLConnector` — in-memory SQLite (`:memory:`) for dev/test; preserves SQL semantics including `PRAGMA query_only` for read-only enforcement.
- `SQLiteConnector` — file-backed via `aiosqlite`; auto-creates parent dirs.
- `PostgresConnector` — async via `asyncpg`; `BEGIN READ ONLY` transaction enforcement; preserves Polars dtypes (BIGINT/DOUBLE PRECISION/BOOLEAN/DATE/TIMESTAMP/TEXT) on `upsert_table`.

**Vector connector implementations**
- `MemoryVectorConnector` — numpy cosine similarity over a Python list; for dev/test.
- `ChromaDBConnector` — `PersistentClient`; embeddings injected externally (`embedding_function=None`); supports filter by `file_id`, `get_chunks` with optional embeddings, `similarity_search_by_vector` for centroid-based semantic-map queries.

**Providers layer**
- `LLMProvider` and `EmbeddingsProvider` ABCs.
- Plugin discovery via entry-points + programmatic registration.
- Per-node model resolution (`resolve_node_model_ref`) with dissertation defaults (planner/verifier=hard, sql/synthesizer=soft); per-node overrides via `[models.<node>]`.

**Provider implementations**
- `OpenAIProvider` (LLM + Embeddings) via `langchain-openai`. Supports structured output.
- `AnthropicProvider` (LLM only) via `langchain-anthropic`. Supports structured output.
- `GoogleProvider` + `GoogleEmbeddingsProvider` via `langchain-google-genai`. Supports structured output.
- `OpenRouterProvider` (LLM only) — OpenAI-compatible at `openrouter.ai`. Conservative `supports_structured_output = False` since model coverage varies.
- `OllamaProvider` + `OllamaEmbeddingsProvider` — OpenAI-compatible local (also handles LMStudio via different `base_url`).

**CLI (Typer + Rich)**
- `hara version` — prints package version.
- `hara connectors list` — Rich table of installed SQL + Vector connectors (built-in + plugins).
- `hara providers list` — Rich table of installed LLM + Embedding providers (built-in + plugins).
- `hara doctor [--config <path>]` — validates `hara.toml`, tests connector connectivity (health checks), checks API keys for configured providers.
- `python -m hara` works as alternate entry point.

**CI/CD**
- Tier 1 workflow (PR gate): lint + format + pyright strict + unit tests + coverage ≥ 80%.
- Tier 2 workflow (contracts + integration): testcontainers-backed Postgres tests, full integration suite.

### Notes
- 84 unit + contract tests passing; 5 Postgres integration tests deselected without Docker (run by CI Tier 2).
- Anthropic does not provide a native embeddings API — users with Anthropic LLMs must configure embeddings from OpenAI or Google.
- Multi-worker uvicorn deferred to v0.2 (will require turn-orphan reaper). v0.1 enforces single-worker `serve`.
