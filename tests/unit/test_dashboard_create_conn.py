"""Shared-connection regression tests for the dashboard DuckDB engine.

Before the fix, query endpoints opened the dashboard DB with read_only=True
while init/refresh paths opened it read_only=False in the same process —
DuckDB rejects mixed configurations on one file, yielding intermittent
"Can't open a connection to same database file with a different
configuration than existing connections" errors in the webapp.
"""

import pytest

from pulse_dashboard.pulse_duckdb.engine import create_conn as cc


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "DUCKDB_PATH", tmp_path / "dash.duckdb")
    monkeypatch.setattr(cc, "DUCKDB_READ_ONLY", False)
    monkeypatch.setattr(cc, "ensure_duckdb_parent_dir", lambda: None)
    cc._master_conn = None
    yield
    if cc._master_conn is not None:
        cc._master_conn.close()
        cc._master_conn = None


def test_mixed_read_only_modes_coexist(isolated_db):
    reader = cc.create_connection(read_only=True)
    writer = cc.create_connection(read_only=False)

    writer.execute("CREATE TABLE t AS SELECT 42 AS x")
    assert reader.execute("SELECT x FROM t").fetchone()[0] == 42

    reader.close()
    writer.close()


def test_closing_a_cursor_leaves_others_usable(isolated_db):
    c1 = cc.create_connection()
    c2 = cc.create_connection()
    c1.close()
    assert c2.execute("SELECT 1").fetchone()[0] == 1
    c2.close()
    # And new handles still work after all cursors are closed.
    c3 = cc.create_connection(read_only=True)
    assert c3.execute("SELECT 1").fetchone()[0] == 1
    c3.close()


def test_dead_master_is_reopened(isolated_db):
    c1 = cc.create_connection()
    c1.close()
    cc._master_conn.close()  # simulate the underlying handle dying
    c2 = cc.create_connection()
    assert c2.execute("SELECT 1").fetchone()[0] == 1
    c2.close()


def test_read_only_deployment_rejects_writable_request(isolated_db, monkeypatch):
    import duckdb

    # Pre-create the file so a read-only open can succeed.
    seed = duckdb.connect(str(cc.DUCKDB_PATH))
    seed.close()
    monkeypatch.setattr(cc, "DUCKDB_READ_ONLY", True)

    with pytest.raises(RuntimeError, match="read-only"):
        cc.create_connection(read_only=False)

    reader = cc.create_connection(read_only=True)
    assert reader.execute("SELECT 1").fetchone()[0] == 1
    reader.close()
