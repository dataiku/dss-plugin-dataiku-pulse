"""Expired-STS-token regression tests for the refreshing DuckDB proxy.

The gold build runs for hours on a DuckDB S3 secret whose session token is a
static snapshot of DSS's resolved credential; when it expired mid-build every
subsequent S3 request failed with "ExpiredToken: The provided token has
expired". The proxy must refresh the secret on a schedule and retry once when
an expiry error slips through mid-statement.
"""

import pytest

from shared_duckdb import refreshing_conn as rc


REAL_ERROR_TEXT = (
    "HTTP Error: HTTP GET error reading 's3://dku-kaos/Pulse/.../silver/...' "
    "in region 'us-east-1' (HTTP 400 Bad Request)\n\n"
    "ExpiredToken: The provided token has expired."
)


class FakeConn:
    def __init__(self, execute_side_effects=None):
        # Each entry is either an Exception to raise or a value to return.
        self.side_effects = list(execute_side_effects or [])
        self.calls = []

    def execute(self, sql, *args, **kwargs):
        self.calls.append(sql)
        if self.side_effects:
            effect = self.side_effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect
        return "ok"

    def close(self):
        self.closed = True


@pytest.fixture()
def refresh_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(rc, "refresh_storage_credentials", lambda conn, *, ctx: calls.append(ctx))
    return calls


def test_expired_token_triggers_refresh_and_retry(refresh_spy):
    conn = FakeConn([RuntimeError(REAL_ERROR_TEXT), "retried"])
    proxy = rc.RefreshingConnection(conn, ctx="ctx")

    assert proxy.execute("SELECT 1") == "retried"
    assert len(refresh_spy) == 1
    assert conn.calls == ["SELECT 1", "SELECT 1"]


def test_non_expiry_errors_propagate_without_refresh(refresh_spy):
    conn = FakeConn([RuntimeError("HTTP 301 Moved Permanently")])
    proxy = rc.RefreshingConnection(conn, ctx="ctx")

    with pytest.raises(RuntimeError, match="301"):
        proxy.execute("SELECT 1")
    assert refresh_spy == []


def test_expired_twice_propagates_after_single_retry(refresh_spy):
    conn = FakeConn([RuntimeError(REAL_ERROR_TEXT), RuntimeError(REAL_ERROR_TEXT)])
    proxy = rc.RefreshingConnection(conn, ctx="ctx")

    with pytest.raises(RuntimeError, match="ExpiredToken"):
        proxy.execute("SELECT 1")
    assert len(refresh_spy) == 1
    assert len(conn.calls) == 2


def test_scheduled_refresh_at_statement_boundaries(refresh_spy):
    conn = FakeConn()
    proxy = rc.RefreshingConnection(conn, ctx="ctx", refresh_interval_seconds=0)

    proxy.execute("SELECT 1")
    proxy.execute("SELECT 2")
    assert len(refresh_spy) == 2


def test_failed_scheduled_refresh_does_not_fail_statement(monkeypatch):
    def boom(conn, *, ctx):
        raise RuntimeError("DSS backend unreachable")

    monkeypatch.setattr(rc, "refresh_storage_credentials", boom)
    conn = FakeConn()
    proxy = rc.RefreshingConnection(conn, ctx="ctx", refresh_interval_seconds=0)

    assert proxy.execute("SELECT 1") == "ok"


def test_attribute_delegation(refresh_spy):
    conn = FakeConn()
    proxy = rc.RefreshingConnection(conn, ctx="ctx")
    proxy.close()
    assert conn.closed


def test_is_expired_credentials_error_markers():
    assert rc.is_expired_credentials_error(RuntimeError(REAL_ERROR_TEXT))
    assert rc.is_expired_credentials_error(RuntimeError("The security token included in the request is expired"))
    assert not rc.is_expired_credentials_error(RuntimeError("HTTP 403 Forbidden"))
    assert not rc.is_expired_credentials_error(RuntimeError("out of memory"))


def test_refresh_storage_credentials_reexecutes_aws_secret(monkeypatch):
    from shared_duckdb import storage_config

    monkeypatch.setattr(storage_config, "aws_credentials", lambda ctx: "CREATE OR REPLACE SECRET s (...)")

    class Ctx:
        connection_type = "EC2"

    conn = FakeConn()
    assert storage_config.refresh_storage_credentials(conn, ctx=Ctx()) is True
    assert conn.calls == ["CREATE OR REPLACE SECRET s (...)"]


def test_refresh_storage_credentials_noop_for_gcs():
    from shared_duckdb import storage_config

    class Ctx:
        connection_type = "GCS"

    conn = FakeConn()
    assert storage_config.refresh_storage_credentials(conn, ctx=Ctx()) is False
    assert conn.calls == []
