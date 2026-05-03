# Contribuindo com o HARA

Obrigado por seu interesse em contribuir com o HARA! Este é um projeto open-source — um agente híbrido multi-agente para consultas SQL + documentos — e toda contribuição é bem-vinda: correções de bugs, novos conectores, melhorias de documentação ou sugestões de design. Para garantir um processo tranquilo, este guia descreve como configurar o ambiente, as convenções de código e o fluxo de trabalho de contribuição.

---

## Código de Conduta

Seja respeitoso, assuma boa fé e critique ideias, não pessoas. Conversas técnicas podem ser acaloradas; mantenha o foco no mérito.

> Um `CODE_OF_CONDUCT.md` formal (Contributor Covenant) está planejado como trabalho futuro.

---

## Setup rápido

```bash
# 1. Clone o repositório
git clone https://github.com/<org>/hara.git
cd hara

# 2. Instale todas as dependências (incluindo extras de dev/test)
uv sync --all-extras

# 3. Copie e ajuste as configurações locais
cp hara.toml.example hara.toml
cp .env.example .env   # adicione suas chaves de API

# 4. Confirme que o ambiente está funcional
uv run hara --help
uv run pytest tests/unit/ -m "not live" -q
```

Não há `pre-commit` configurado ainda. Antes de cada commit, rode manualmente:

```bash
uv run ruff check src/ tests/          # lint
uv run ruff format src/ tests/         # formatação
uv run pyright src/hara/               # verificação de tipos
```

---

## Estrutura do projeto

```
src/hara/
├── agent/           # pipeline multi-agente LangGraph
├── api/             # camada HTTP FastAPI
├── cli/             # comandos Typer
├── config/          # configurações via Pydantic-Settings
├── connectors/      # backends SQL e Vetorial
├── ingest/          # CSV/PDF/DOCX/MD/TXT → connectors
├── providers/       # adaptadores LLM e embedding
├── services/        # serviços transversais (semantic_map, session_store)
└── utils/
tests/
├── unit/            # testes rápidos, sem I/O externo
├── contracts/       # testes de contrato de conectores (testcontainers)
└── integration/     # fluxos end-to-end
docs/
├── extending/       # guias de extensão (connectors.md, providers.md…)
├── superpowers/     # specs e planos internos de desenvolvimento (não user-facing)
├── api.md
├── configuration.md
└── getting-started.md
```

---

## Fluxo de trabalho

1. **Crie um branch** a partir de `main` com nome descritivo:
   ```bash
   git switch -c feat/my-feature
   ```

2. **Faça commits** seguindo [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat: adiciona conector Qdrant`
   - `fix: corrige paginação no SQL connector`
   - `docs: atualiza getting-started.md`
   - `test: adiciona contrato para VectorConnector`
   - `refactor:`, `chore:`, `perf:` conforme necessário

3. **Abra um Pull Request** com título claro e descrição do que foi feito e por quê.

---

## Testes

Toda nova feature deve vir acompanhada de testes. Adotamos o padrão TDD:

1. Escreva um teste que falha para o comportamento desejado.
2. Implemente o código mínimo para fazê-lo passar.
3. Refatore com os testes verdes.

O pytest-asyncio está configurado com `mode = "auto"` — funções `async def test_*` são executadas automaticamente como corrotinas sem precisar de decorator adicional.

### Tiers de CI

| Tier | Quando | Comando |
|------|--------|---------|
| 1 — PR gate (<2 min) | todo PR | `uv run pytest tests/unit/ -m "not live"` + ruff + pyright |
| 2 — PR non-blocking | todo PR | `uv run pytest tests/contracts/ tests/integration/ -m "not live"` (testcontainers) |
| 3 — Nightly / opt-in | agendado | `uv run pytest -m live` (chamadas reais a LLMs) |

Para rodar apenas o Tier 1 localmente:

```bash
uv run pytest tests/unit/ -m "not live" -v
```

Para rodar um arquivo específico:

```bash
uv run pytest tests/unit/test_session_store.py -v
```

**Testes de contrato** em `tests/contracts/` sobem containers reais via Testcontainers. Requerem Docker rodando localmente e são mais lentos — rode antes de abrir um PR que altere conectores:

```bash
uv run pytest tests/contracts/ -v
```

---

## Verificação de tipos e lint

O projeto usa **Pyright no modo strict** para todo código em `src/hara/`. Nenhum PR será integrado com erros de tipo.

```bash
uv run pyright src/hara/
```

Se precisar suprimir um erro, use `# pyright: ignore[<rule>]` **com um comentário explicando o motivo**:

```python
result = some_untyped_lib_call()  # pyright: ignore[reportUnknownVariableType] — lib sem stubs
```

O Ruff cuida de lint e formatação. A maioria dos problemas é corrigida automaticamente:

```bash
uv run ruff check src/ tests/ --fix
uv run ruff format src/ tests/
```

---

## Adicionando um novo connector ou provider

Consulte `docs/extending/connectors.md` para o guia completo de extensão, incluindo a interface esperada, registro e testes de contrato obrigatórios.

---

## Documentação

- Documentação voltada ao usuário fica em `docs/` e é escrita em **português brasileiro**.
- Specs e planos internos de desenvolvimento ficam em `docs/superpowers/` e não são user-facing.
- Ao adicionar uma feature nova, atualize ou crie o arquivo `.md` correspondente em `docs/`.

---

## Processo de release

1. Atualize a versão em `pyproject.toml`.
2. Adicione uma entrada em `CHANGELOG.md` descrevendo as mudanças.
3. Crie uma tag Git:
   ```bash
   git tag -a vX.Y.Z -m "release vX.Y.Z"
   git push origin vX.Y.Z
   ```
4. Construa o pacote:
   ```bash
   uv build
   ```
5. Publique (quando o projeto atingir PyPI):
   ```bash
   uv publish
   ```

> O projeto ainda não está publicado no PyPI — esta etapa é para quando chegarmos lá.

---

## Convenções de issue e PR

- **Título claro e conciso** — descreva o problema ou a mudança em uma frase.
- **Bugs**: inclua versão do Python, sistema operacional, comando executado, saída completa do erro e passos para reproduzir.
- **Features**: descreva o caso de uso e, se possível, a interface proposta.
- **Saída do CLI (Rich)**: inclua screenshot ou texto copiado do terminal para facilitar revisão visual.

---

## Onde tirar dúvidas

Abra uma [issue](https://github.com/<org>/hara/issues) para bugs e sugestões, ou inicie uma [Discussion](https://github.com/<org>/hara/discussions) para perguntas mais abertas.
