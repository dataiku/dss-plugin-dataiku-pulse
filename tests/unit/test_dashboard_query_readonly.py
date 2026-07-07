"""Read-only enforcement tests for the dashboard query path.

create_connection hands out cursors on one shared *writable* connection, so
query_df (used by the /api/duckdb/query debug endpoint, among others) must
reject write statements itself — the connection mode no longer does.
"""

import duckdb
import pytest

from pulse_dashboard.pulse_duckdb.engine import query as q


@pytest.fixture()
def mem_engine(monkeypatch):
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE t AS SELECT 42 AS x")
    monkeypatch.setattr(q, "create_connection", lambda read_only=None: conn.cursor())
    yield conn
    conn.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT x FROM t",
        "  select 1  ",
        "WITH c AS (SELECT 1 AS n) SELECT * FROM c",
        "SHOW TABLES",
        "DESCRIBE t",
        "SUMMARIZE t",
        "EXPLAIN SELECT 1",
        "/* comment */ SELECT 1 -- trailing",
        "FROM t",
    ],
)
def test_read_statements_pass(mem_engine, sql):
    q.query_df(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 0",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE u(a INT)",
        "COPY t TO 'out.csv'",
        "ATTACH ':memory:' AS m2",
        "SET memory_limit='1GB'",
        "CALL pragma_version()",
        # Writes may not hide behind a leading read in a multi-statement batch.
        "SELECT 1; DROP TABLE t",
        "BEGIN; DELETE FROM t; COMMIT",
    ],
)
def test_write_statements_rejected(mem_engine, sql):
    with pytest.raises(q.ReadOnlySQLError):
        q.query_df(sql)
    # And nothing was executed: the table is intact.
    assert q.query_df("SELECT count(*) AS n FROM t")["n"].iloc[0] == 1


def test_params_still_work(mem_engine):
    df = q.query_df("SELECT x FROM t WHERE x = ?", [42])
    assert df["x"].iloc[0] == 42
