from __future__ import annotations

import logging
from pathlib import Path

import pytest

from shared_duckdb import extensions as ext


class FakeConnection:
    def __init__(self, responses: list[object]):
        self._responses = list(responses)
        self.commands: list[str] = []

    def execute(self, sql: str):
        self.commands.append(sql)
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, BaseException):
                raise response
        return self


@pytest.fixture()
def resource_tree(tmp_path: Path) -> Path:
    resource_dir = tmp_path / "resource"
    version_dir = resource_dir / "duckdb_extensions" / "1.5.3" / "linux_amd64"
    version_dir.mkdir(parents=True)
    (version_dir / "httpfs.duckdb_extension").write_text("httpfs", encoding="utf-8")
    (version_dir / "azure.duckdb_extension").write_text("azure", encoding="utf-8")
    return resource_dir


@pytest.fixture(autouse=True)
def stable_platform_and_version(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ext, "duckdb_version", lambda: "1.5.3")
    monkeypatch.setattr(ext, "platform_slug", lambda: "linux_amd64")


@pytest.fixture(autouse=True)
def native_resolvers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ext, "_NATIVE_RESOURCE_RESOLVERS", (("webapp.api", "webapp"), ("recipe.api", "recipe")))


def test_bundled_extension_path_prefers_webapp_resource(resource_tree: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def fake_native_api(**kwargs):
        calls.append(kwargs["source_name"])
        return (resource_tree, None) if kwargs["source_name"] == "webapp" else (resource_tree / "unused", None)

    monkeypatch.setattr(ext, "_resource_dir_from_native_api", fake_native_api)

    assert ext.bundled_extension_path("httpfs") == resource_tree / "duckdb_extensions" / "1.5.3" / "linux_amd64" / "httpfs.duckdb_extension"
    assert calls == ["webapp"]


def test_bundled_extension_path_uses_recipe_resource_when_webapp_unavailable(resource_tree: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_native_api(**kwargs):
        if kwargs["source_name"] == "webapp":
            return None, None
        return resource_tree, None

    monkeypatch.setattr(ext, "_resource_dir_from_native_api", fake_native_api)

    assert ext.bundled_extension_path("azure") == resource_tree / "duckdb_extensions" / "1.5.3" / "linux_amd64" / "azure.duckdb_extension"


def test_bundled_extension_path_uses_filesystem_fallback(resource_tree: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ext, "_resource_dir_from_native_api", lambda **kwargs: (None, None))
    monkeypatch.setattr(ext, "_local_repo_resource_dir", lambda: resource_tree)

    assert ext.bundled_extension_path("httpfs") == resource_tree / "duckdb_extensions" / "1.5.3" / "linux_amd64" / "httpfs.duckdb_extension"


def test_empty_native_resource_paths_fall_through_to_recipe(resource_tree: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_native_api(**kwargs):
        if kwargs["source_name"] == "webapp":
            return None, None
        return resource_tree, None

    monkeypatch.setattr(ext, "_resource_dir_from_native_api", fake_native_api)

    assert ext.bundled_extension_path("httpfs") == resource_tree / "duckdb_extensions" / "1.5.3" / "linux_amd64" / "httpfs.duckdb_extension"


def test_native_resolver_exception_is_logged_and_falls_back(resource_tree: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    def fake_native_api(**kwargs):
        if kwargs["source_name"] == "webapp":
            return None, "webapp: RuntimeError: boom"
        return resource_tree, None

    monkeypatch.setattr(ext, "_resource_dir_from_native_api", fake_native_api)

    with caplog.at_level(logging.WARNING):
        path = ext.bundled_extension_path("httpfs")

    assert path == resource_tree / "duckdb_extensions" / "1.5.3" / "linux_amd64" / "httpfs.duckdb_extension"
    assert "webapp: RuntimeError: boom" not in caplog.text


def test_missing_resource_dir_includes_native_failure_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    missing_resource = tmp_path / "missing-resource"

    def fake_native_api(**kwargs):
        if kwargs["source_name"] == "webapp":
            return None, "webapp: RuntimeError: boom"
        return None, None

    monkeypatch.setattr(ext, "_resource_dir_from_native_api", fake_native_api)
    monkeypatch.setattr(ext, "_local_repo_resource_dir", lambda: missing_resource)

    with pytest.raises(FileNotFoundError, match="Native resource resolution failures: webapp: RuntimeError: boom"):
        ext.bundled_extension_path("httpfs")


def test_bundled_extension_path_reports_missing_duckdb_extensions_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    resource_dir = tmp_path / "resource"
    resource_dir.mkdir()
    monkeypatch.setattr(ext, "_resource_dir_from_native_api", lambda **kwargs: (resource_dir, None) if kwargs["source_name"] == "webapp" else (None, None))

    with pytest.raises(FileNotFoundError, match="bundled extensions directory unavailable"):
        ext.bundled_extension_path("httpfs")


def test_bundled_extension_path_reports_missing_version_platform_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    resource_dir = tmp_path / "resource"
    bundle_root = resource_dir / "duckdb_extensions" / "9.9.9" / "linux_amd64"
    bundle_root.mkdir(parents=True)
    monkeypatch.setattr(ext, "_resource_dir_from_native_api", lambda **kwargs: (resource_dir, None) if kwargs["source_name"] == "webapp" else (None, None))

    with pytest.raises(FileNotFoundError, match="Bundled version/platform matrix: 9.9.9/linux_amd64"):
        ext.bundled_extension_path("httpfs")


def test_bundled_extension_path_does_not_select_wrong_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    resource_dir = tmp_path / "resource"
    wrong_version_dir = resource_dir / "duckdb_extensions" / "9.9.9" / "linux_amd64"
    wrong_version_dir.mkdir(parents=True)
    (wrong_version_dir / "httpfs.duckdb_extension").write_text("wrong", encoding="utf-8")
    monkeypatch.setattr(ext, "_resource_dir_from_native_api", lambda **kwargs: (resource_dir, None) if kwargs["source_name"] == "webapp" else (None, None))

    with pytest.raises(FileNotFoundError, match="duckdb 1.5.3 on platform linux_amd64"):
        ext.bundled_extension_path("httpfs")


def test_bundled_extension_path_does_not_select_wrong_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    resource_dir = tmp_path / "resource"
    wrong_platform_dir = resource_dir / "duckdb_extensions" / "1.5.3" / "linux_arm64"
    wrong_platform_dir.mkdir(parents=True)
    (wrong_platform_dir / "httpfs.duckdb_extension").write_text("wrong", encoding="utf-8")
    monkeypatch.setattr(ext, "_resource_dir_from_native_api", lambda **kwargs: (resource_dir, None) if kwargs["source_name"] == "webapp" else (None, None))

    with pytest.raises(FileNotFoundError, match="Bundled version/platform matrix: 1.5.3/linux_arm64"):
        ext.bundled_extension_path("httpfs")


def test_invalid_extension_names_are_rejected_before_sql_execution():
    conn = FakeConnection([None])

    with pytest.raises(ValueError, match="Unsupported DuckDB extension"):
        ext.ensure_extension_loaded(conn, "httpfs; DROP TABLE x;")

    assert conn.commands == []


def test_valid_extension_names_continue_to_work():
    conn = FakeConnection([None, None])

    assert ext.ensure_extension_loaded(conn, "httpfs") == "installed"
    assert conn.commands == ["INSTALL httpfs;", "LOAD httpfs;"]


def test_ensure_extension_loaded_returns_cached_after_install_failure():
    conn = FakeConnection([RuntimeError("offline"), None])

    assert ext.ensure_extension_loaded(conn, "httpfs") == "cached"
    assert conn.commands == ["INSTALL httpfs;", "LOAD httpfs;"]


def test_ensure_extension_loaded_returns_bundled_after_install_and_cache_fail(resource_tree: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ext, "_resource_dir_from_native_api", lambda **kwargs: (resource_tree, None) if kwargs["source_name"] == "webapp" else (None, None))
    conn = FakeConnection([RuntimeError("offline"), RuntimeError("not cached"), None])

    assert ext.ensure_extension_loaded(conn, "httpfs") == "bundled"
    assert conn.commands[0:2] == ["INSTALL httpfs;", "LOAD httpfs;"]
    assert conn.commands[2].endswith("/resource/duckdb_extensions/1.5.3/linux_amd64/httpfs.duckdb_extension';")


def test_ensure_extension_loaded_supports_azure():
    conn = FakeConnection([None, None])

    assert ext.ensure_extension_loaded(conn, "azure") == "installed"
    assert conn.commands == ["INSTALL azure;", "LOAD azure;"]


def test_ensure_extension_loaded_raises_when_bundled_extension_missing(resource_tree: Path, monkeypatch: pytest.MonkeyPatch):
    (resource_tree / "duckdb_extensions" / "1.5.3" / "linux_amd64" / "azure.duckdb_extension").unlink()
    monkeypatch.setattr(ext, "_resource_dir_from_native_api", lambda **kwargs: (resource_tree, None) if kwargs["source_name"] == "webapp" else (None, None))
    conn = FakeConnection([RuntimeError("offline"), RuntimeError("not cached")])

    with pytest.raises(FileNotFoundError, match="No bundled DuckDB extension 'azure'"):
        ext.ensure_extension_loaded(conn, "azure")
