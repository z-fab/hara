# Estendendo o HARA: escrevendo um connector

Este walkthrough mostra como adicionar um novo connector SQL ao
HARA — usando **MySQL** como exemplo — em menos de uma hora, de ponta
a ponta, com contract tests passando ao final. O mesmo padrão se
aplica a vector stores (Pinecone, Qdrant, Weaviate, etc.), abordados
brevemente no final.

> Ao final, você terá um pacote `mysql_connector` fornecendo uma
> subclasse `MysqlConnector(SQLConnector)`, registrada via entry-point,
> e testada via o mixin reutilizável `SQLConnectorContractTests`.

## Por que connectors?

HARA é **storage-agnostic por design.** O SQL Executor do agente e o
serviço de Ingest falam apenas com a ABC `SQLConnector`; o mesmo vale
para texto via `VectorConnector`. Qualquer coisa que consiga responder
"me dê o resultado de um SELECT" ou "embeda e armazene esses chunks"
pode ser plugada.

Há duas formas de registrar:

1. **Entry-points** (recomendado para pacotes distribuíveis) —
   adicione `[project.entry-points."hara.connectors.sql"]` ao seu
   `pyproject.toml` e faça `pip install` do seu pacote junto com o
   HARA. O connector fica disponível pelo nome (`type = "mysql"` em
   `hara.toml`).
2. **Programático** — `register_sql_connector("mysql", MysqlConnector)`
   em runtime. Útil para uso in-process (notebooks, apps customizados),
   testes ou experimentação rápida antes de publicar.

Registro programático **tem precedência sobre entry-points** quando
ambos definem o mesmo nome.

## O contrato base

```python
from hara.connectors.sql.base import SQLConnector
```

Uma subclasse `SQLConnector` implementa:

### Abstratos (override obrigatório)

- `from_config(cls, config)` — factory classmethod; recebe o bloco de
  config Pydantic validado (`SQLConnectorConfigUnion`).
- `execute_query(sql, *, read_only=True)` — executa um único SELECT.
  Deve enforçar read-only no **nível de transação** (não apenas
  torcendo para o LLM se comportar).
- `upsert_table(table_name, df, *, mode="replace")` — usado pelo
  serviço de Ingest para escrever CSVs.
- `health_check()` — probe barato; aparece em `hara doctor`.
- `dialect` (property) — identificador string (`"sqlite"`,
  `"postgres"`, `"mysql"`, ...). O SQL Executor passa isso para o
  sqlglot para parsing + validação de AST.

### Primitivos do template-method (override; você ganha `list_tables` de graça)

- `_list_table_names()` — lista tabelas visíveis ao usuário (filtra
  schemas de sistema / `information_schema`).
- `_get_table_columns(table_name)` — lista `ColumnInfo(name, type)`
  para a tabela.
- `_read_table_dataframe(table_name)` — carrega todas as linhas como
  um DataFrame Polars. Usado pelo `list_tables` padrão para computar
  estatísticas.

### O que você ganha de graça

Implementando apenas os três primitivos, `list_tables()` retorna
automaticamente uma lista de `TableInfo` com **estatísticas por
coluna**:

```python
TableInfo(
    name="conab_safras",
    columns=[ColumnInfo("uf", "TEXT"), ColumnInfo("producao_t", "BIGINT")],
    row_count=5,
    column_statistics={
        "uf": ColumnStatistics(
            distinct_count=4,
            top_values=["MT", "PR", "RS", "GO"],
            all_unique_values=["MT", "PR", "RS", "GO"],  # populated when distinct ≤ 20
        ),
        "producao_t": ColumnStatistics(
            row_count=5,
            min=11200000.0, max=42500000.0, mean=22120000.0,
            null_percentage=0.0,
        ),
    },
)
```

Essas estatísticas alimentam o passo de semantic-map → `structured.yaml`
→ que o agente lê no momento de planejamento.

Se seu storage tem uma rota nativa mais rápida para stats (pense no
sample do `pg_stats` em Postgres, ou tabelas particionadas grandes
demais para carregar inteiras), faça override de `list_tables()`
diretamente. Apenas garanta que o contract test
`test_list_tables_emits_column_statistics` ainda passa (isto é, que
você ainda preenche `column_statistics`).

---

## Exemplo MySQL, passo a passo

### Passo 1. Definir o schema de configuração

No seu pacote de extensão (vamos chamá-lo de `hara_mysql`), declare um
schema Pydantic com um discriminator:

```python
# hara_mysql/config.py
from pydantic import BaseModel, SecretStr
from typing import Literal


class MysqlSQLConfig(BaseModel):
    type: Literal["mysql"]
    url: SecretStr  # mysql://user:pass@host:3306/dbname
```

> O `SQLConnectorConfigUnion` do HARA é fechado para extensão na v0.1
> — sua config é validada pelo `from_config` do seu próprio connector,
> não pelo tagged-union central. Tudo bem: o loader repassa qualquer
> bloco `[connectors.sql]` cujo `type` resolva via o registry.
> Estritamente, você receberá um dict genérico via
> `SQLConnectorConfigUnion` — veja `from_config` abaixo.

### Passo 2. Subclassear `SQLConnector`

```python
# hara_mysql/connector.py
from __future__ import annotations

from typing import Any

import aiomysql  # pip install aiomysql
import polars as pl

from hara.config.schemas import SQLConnectorConfigUnion
from hara.connectors.sql.base import (
    ColumnInfo,
    ConnectorHealth,
    SQLConnector,
    SQLResult,
)
from hara_mysql.config import MysqlSQLConfig


class MysqlConnector(SQLConnector):
    def __init__(self, dsn: str) -> None:
        # Expected form: "mysql://user:pass@host:3306/dbname"
        self._dsn = dsn

    @classmethod
    def from_config(cls, config: SQLConnectorConfigUnion) -> "MysqlConnector":
        # The central union doesn't know about MysqlSQLConfig — so we
        # validate against our own schema here.
        if isinstance(config, MysqlSQLConfig):
            return cls(dsn=config.url.get_secret_value())
        # If HARA passes a dict (3rd-party type not in the union),
        # validate explicitly:
        validated = MysqlSQLConfig.model_validate(
            config if isinstance(config, dict) else config.model_dump()
        )
        return cls(dsn=validated.url.get_secret_value())

    async def _connect(self) -> aiomysql.Connection:
        # Quick-and-dirty DSN parse. Production code: use sqlalchemy.URL.
        from urllib.parse import urlparse
        parsed = urlparse(self._dsn)
        return await aiomysql.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=parsed.username or "",
            password=parsed.password or "",
            db=(parsed.path or "/").lstrip("/"),
            autocommit=False,
        )

    async def execute_query(self, sql: str, *, read_only: bool = True) -> SQLResult:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                if read_only:
                    # MySQL: SET TRANSACTION READ ONLY; START TRANSACTION;
                    await cur.execute("SET TRANSACTION READ ONLY")
                    await cur.execute("START TRANSACTION")
                try:
                    await cur.execute(sql)
                    raw_rows = await cur.fetchall()
                    columns = [d[0] for d in cur.description] if cur.description else []
                    rows: list[tuple[Any, ...]] = [tuple(r) for r in raw_rows]
                finally:
                    if read_only:
                        await cur.execute("ROLLBACK")
            return SQLResult(columns=columns, rows=rows, dialect="mysql")
        finally:
            conn.close()

    async def _list_table_names(self) -> list[str]:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()"
                )
                rows = await cur.fetchall()
                return [str(r[0]) for r in rows]
        finally:
            conn.close()

    async def _get_table_columns(self, table_name: str) -> list[ColumnInfo]:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s",
                    (table_name,),
                )
                rows = await cur.fetchall()
                return [ColumnInfo(name=str(r[0]), type=str(r[1])) for r in rows]
        finally:
            conn.close()

    async def _read_table_dataframe(self, table_name: str) -> pl.DataFrame:
        conn = await self._connect()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(f'SELECT * FROM `{table_name}`')
                records = await cur.fetchall()
        finally:
            conn.close()
        if not records:
            return pl.DataFrame()
        return pl.from_dicts([dict(r) for r in records])

    async def upsert_table(
        self, table_name: str, df: pl.DataFrame, *, mode: str = "replace"
    ) -> None:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                if mode == "replace":
                    await cur.execute(f'DROP TABLE IF EXISTS `{table_name}`')
                elif mode == "skip":
                    await cur.execute(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema=DATABASE() AND table_name=%s",
                        (table_name,),
                    )
                    if await cur.fetchone():
                        return
                col_defs = ", ".join(
                    f'`{n}` {_polars_to_mysql_type(t)}'
                    for n, t in zip(df.columns, df.dtypes, strict=True)
                )
                await cur.execute(
                    f'CREATE TABLE IF NOT EXISTS `{table_name}` ({col_defs})'
                )
                placeholders = ", ".join(["%s"] * len(df.columns))
                rows = [tuple(r) for r in df.iter_rows()]
                await cur.executemany(
                    f'INSERT INTO `{table_name}` VALUES ({placeholders})',
                    rows,
                )
            await conn.commit()
        finally:
            conn.close()

    async def health_check(self) -> ConnectorHealth:
        try:
            conn = await self._connect()
            try:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                return ConnectorHealth(ok=True, message="mysql ready")
            finally:
                conn.close()
        except Exception as e:
            return ConnectorHealth(ok=False, message=f"mysql failed: {e}")

    @property
    def dialect(self) -> str:
        return "mysql"


def _polars_to_mysql_type(dtype: pl.DataType) -> str:
    if dtype.is_integer():
        return "BIGINT"
    if dtype.is_float():
        return "DOUBLE"
    if dtype == pl.Boolean:
        return "TINYINT(1)"
    return "TEXT"
```

São ~110 linhas, incluindo o helper de mapeamento de tipos.

### Passo 3. Registrar o entry-point

No `pyproject.toml` do seu pacote:

```toml
[project]
name = "hara-mysql"
version = "0.1.0"
dependencies = [
    "hara>=0.1",
    "aiomysql>=0.2",
    "polars>=1.0",
]

[project.entry-points."hara.connectors.sql"]
mysql = "hara_mysql.connector:MysqlConnector"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Após `pip install -e .`, seu connector aparece:

```bash
$ hara connectors list
SQL connectors:
  - sqlite       (built-in)
  - postgres     (built-in)
  - memory       (built-in)
  - mysql        (hara-mysql)        ← novo
Vector connectors:
  - chromadb     (built-in)
  - memory       (built-in)
```

E usuários podem agora escrever:

```toml
[connectors.sql]
type = "mysql"
url = "mysql://hara:hara@localhost:3306/hara"
```

### Passo 4. Escrever os contract tests

O HARA já fornece um mixin reutilizável `SQLConnectorContractTests`.
Herde dele e forneça uma fixture `connector` que sobe um MySQL novo —
tipicamente via [testcontainers](https://github.com/testcontainers/testcontainers-python):

```python
# tests/contracts/test_mysql.py
import pytest
from testcontainers.mysql import MySqlContainer  # pip install testcontainers[mysql]

from hara.connectors.sql.testing import SQLConnectorContractTests
from hara_mysql.connector import MysqlConnector


@pytest.fixture(scope="module")
def mysql_container():
    with MySqlContainer("mysql:8.0") as mysql:
        yield mysql


class TestMysqlConnectorContract(SQLConnectorContractTests):
    @pytest.fixture
    async def connector(self, mysql_container) -> MysqlConnector:
        url = mysql_container.get_connection_url().replace("mysql+pymysql", "mysql")
        return MysqlConnector(dsn=url)
```

É isso. Herdar `SQLConnectorContractTests` te dá 6 testes:

1. `test_health_check_passes`
2. `test_dialect_is_string`
3. `test_upsert_and_query_table`
4. `test_list_tables_after_upsert`
5. `test_list_tables_emits_column_statistics`
6. `test_read_only_blocks_writes`

### Passo 5. Executá-los

```bash
$ pytest tests/contracts/test_mysql.py -v
============================= test session starts =============================
collected 6 items

tests/contracts/test_mysql.py::TestMysqlConnectorContract::test_health_check_passes PASSED
tests/contracts/test_mysql.py::TestMysqlConnectorContract::test_dialect_is_string PASSED
tests/contracts/test_mysql.py::TestMysqlConnectorContract::test_upsert_and_query_table PASSED
tests/contracts/test_mysql.py::TestMysqlConnectorContract::test_list_tables_after_upsert PASSED
tests/contracts/test_mysql.py::TestMysqlConnectorContract::test_list_tables_emits_column_statistics PASSED
tests/contracts/test_mysql.py::TestMysqlConnectorContract::test_read_only_blocks_writes PASSED

============================== 6 passed in 18.4s ==============================
```

Se um teste falha, a mensagem aponta exatamente qual comportamento do
contrato quebrou (ex.: `test_read_only_blocks_writes` falhando
significa que seu `execute_query` não está enforçando read-only
corretamente).

---

## E o VectorConnector?

Mesmo formato, superfície menor. Subclasse `VectorConnector`
(`hara.connectors.vector.base`) e implemente:

- `from_config(cls, config, embedder)` — note que o embedder é **injetado**.
- `similarity_search(query, *, k, filter)` — embeda + busca por vetor.
- `similarity_search_by_vector(vector, *, k, filter)` — busca por
  vetor cru (usado pelo passo de centroide do semantic-map).
- `upsert_chunks(chunks)` — upsert em batch.
- `list_documents()` — file_ids distintos + contagem de chunks.
- `delete_document(file_id)` — delete em cascata.
- `get_chunks(file_id, *, include_embeddings)` — recupera de volta.
- `health_check()`.

Veja as implementações built-in `chromadb`
(`src/hara/connectors/vector/chromadb.py`) e `memory`
(`src/hara/connectors/vector/memory.py`) como referência de formato e
edge-cases.

Os contract tests ficam no mesmo módulo dos de SQL:

```python
from hara.connectors.vector.testing import VectorConnectorContractTests
```

…na verdade, isso é um typo neste rascunho da doc — eles ficam em
`hara.connectors.testing.VectorConnectorContractTests`. Use:

```python
from hara.connectors.testing import VectorConnectorContractTests
```

Expõe 8 testes cobrindo similarity search (texto e vetor), filtragem
por file_id, round-trips de embedding, deleção e listagem.

Grupo de entry-point para vector connectors:

```toml
[project.entry-points."hara.connectors.vector"]
qdrant = "hara_qdrant.connector:QdrantConnector"
```

---

## Registro programático

Se você não quer publicar um pacote — digamos que está embutindo o
HARA em um app Python maior — registre em runtime:

```python
from hara.connectors import register_sql_connector, register_vector_connector
from your_pkg import MysqlConnector, QdrantConnector

register_sql_connector("mysql", MysqlConnector)
register_vector_connector("qdrant", QdrantConnector)

# agora `type = "mysql"` e `type = "qdrant"` resolvem em hara.toml
```

Registro programático tem precedência sobre entry-points quando ambos
definem o mesmo nome — útil para monkey-patching em testes.

---

## Sanity check final

Rode `hara doctor` — seu novo tipo de connector deve aparecer na linha
`connector.sql` com status `OK`. Rode `hara ingest --from data/raw` e
verifique as tabelas pelo CLI `mysql` diretamente:

```bash
$ mysql -u hara -p -h localhost hara
> SHOW TABLES;
+----------------+
| Tables_in_hara |
+----------------+
| conab_safras   |
+----------------+
```

Pronto. Daqui em diante, packaging + publicação no PyPI é processo
padrão do ecossistema Python (`uv build && uv publish`, ou `python -m
build && twine upload dist/*`).

---

## Referências

- [Source do `SQLConnector`](../../src/hara/connectors/sql/base.py)
- [Source do `VectorConnector`](../../src/hara/connectors/vector/base.py)
- [Contract tests](../../src/hara/connectors/testing.py)
- Referências built-in:
  [`SQLiteConnector`](../../src/hara/connectors/sql/sqlite.py),
  [`PostgresConnector`](../../src/hara/connectors/sql/postgres.py),
  [`ChromaDBConnector`](../../src/hara/connectors/vector/chromadb.py)
- Helpers de registry:
  [`hara.connectors._registry`](../../src/hara/connectors/_registry.py)
