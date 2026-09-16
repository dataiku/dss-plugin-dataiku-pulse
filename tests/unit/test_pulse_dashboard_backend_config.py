from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any


BACKEND_PATH = Path(__file__).resolve().parents[2] / "webapps" / "pulse-dashboard" / "backend.py"


def _load_backend(
    monkeypatch,
    *,
    webapp_config: dict[str, Any] | None = None,
    webapp_exception: Exception | None = None,
    local_config: Path | None = None,
):
    module_name = "pulse_dashboard_webapp_backend_under_test"
    sys.modules.pop(module_name, None)

    fake_backend_package = types.ModuleType("pulse_dashboard.webapp_backend")
    fake_backend_package.register_local_routes = lambda app: None
    fake_backend_package.register_routes = lambda app: None
    monkeypatch.setitem(sys.modules, "pulse_dashboard.webapp_backend", fake_backend_package)

    customwebapp = types.ModuleType("dataiku.customwebapp")
    if webapp_exception is not None:
        customwebapp.get_webapp_config = lambda: (_ for _ in ()).throw(webapp_exception)
    elif webapp_config is not None:
        customwebapp.get_webapp_config = lambda: webapp_config
    monkeypatch.setitem(sys.modules, "dataiku.customwebapp", customwebapp)

    if local_config is not None:
        monkeypatch.setenv("PULSE_DASHBOARD_LOCAL_PLUGIN_CONFIG", str(local_config))
    else:
        monkeypatch.delenv("PULSE_DASHBOARD_LOCAL_PLUGIN_CONFIG", raising=False)

    spec = importlib.util.spec_from_file_location(module_name, BACKEND_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_backend_uses_native_webapp_pulse_primary(monkeypatch, tmp_path: Path):
    fallback_path = tmp_path / "plugin_config.json"
    fallback_path.write_text(json.dumps({"pulse_primary": {"pulse_project_key": "LOCAL"}}), encoding="utf-8")

    module = _load_backend(
        monkeypatch,
        webapp_config={"pulse_primary": {"pulse_project_key": "NATIVE"}},
        local_config=fallback_path,
    )

    assert module.pulse_primary == {"pulse_project_key": "NATIVE"}
    assert module.app.config["_PULSE_DASHBOARD_PULSE_PRIMARY"] == {"pulse_project_key": "NATIVE"}


def test_backend_falls_back_to_local_plugin_config_when_webapp_loader_missing(monkeypatch, tmp_path: Path):
    fallback_path = tmp_path / "plugin_config.json"
    fallback_path.write_text(json.dumps({"pulse_primary": {"pulse_project_key": "LOCAL"}}), encoding="utf-8")

    module = _load_backend(monkeypatch, webapp_config=None, local_config=fallback_path)

    assert module.pulse_primary == {"pulse_project_key": "LOCAL"}


def test_backend_falls_back_when_local_webapp_config_env_is_missing(monkeypatch, tmp_path: Path):
    fallback_path = tmp_path / "plugin_config.json"
    fallback_path.write_text(json.dumps({"pulse_primary": {"pulse_project_key": "LOCAL"}}), encoding="utf-8")

    module = _load_backend(
        monkeypatch,
        webapp_exception=TypeError("the JSON object must be str, bytes or bytearray, not NoneType"),
        local_config=fallback_path,
    )
    assert module.pulse_primary == {"pulse_project_key": "LOCAL"}


def test_backend_rejects_missing_pulse_primary_in_local_plugin_config(monkeypatch, tmp_path: Path):
    fallback_path = tmp_path / "plugin_config.json"
    fallback_path.write_text(json.dumps({}), encoding="utf-8")

    try:
        _load_backend(monkeypatch, webapp_config=None, local_config=fallback_path)
    except RuntimeError as exc:
        assert "missing pulse_primary" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected missing local pulse_primary to fail")
