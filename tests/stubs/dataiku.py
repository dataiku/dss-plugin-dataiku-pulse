"""Stub `dataiku` module for unit tests.

The real `dataiku` package only exists inside a DSS runtime. Tests import
plugin code that does `import dataiku` / `from dataiku.runnables import ...`
at module level; this stub satisfies those imports with inert placeholders.
Submodules are registered in sys.modules at import time so
`from dataiku.runnables import Runnable` resolves against a plain module.
"""

from __future__ import annotations

import sys
import types
from typing import Any


class Runnable:
    def __init__(self, project_key=None, config=None, plugin_config=None):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}


class ResultTable:
    def __init__(self) -> None:
        self.columns: list[tuple[int, str, str]] = []
        self.records: list[list[Any]] = []

    def add_column(self, index: int, name: str, col_type: str) -> None:
        self.columns.append((index, name, col_type))

    def add_record(self, record: list[Any]) -> None:
        self.records.append(list(record))


class Folder:
    def __init__(self, lookup: str, project_key: str | None = None, ignore_flow: bool = False):
        self.lookup = lookup
        self.project_key = project_key

    def upload_stream(self, path: str, data: Any) -> None:  # pragma: no cover
        raise NotImplementedError("stub Folder cannot upload")


def api_client() -> Any:  # pragma: no cover - tests pass explicit fakes instead
    raise NotImplementedError("stub dataiku has no API client")


def default_project_key() -> str:
    return "TEST_PROJECT"


_runnables = types.ModuleType("dataiku.runnables")
_runnables.Runnable = Runnable
_runnables.ResultTable = ResultTable
sys.modules["dataiku.runnables"] = _runnables

_customrecipe = types.ModuleType("dataiku.customrecipe")


def get_recipe_config() -> dict:
    return {}


def get_output_names_for_role(role: str) -> list[str]:
    return []


_customrecipe.get_recipe_config = get_recipe_config
_customrecipe.get_output_names_for_role = get_output_names_for_role


def get_recipe_resource() -> str | None:
    return None


_customrecipe.get_recipe_resource = get_recipe_resource
sys.modules["dataiku.customrecipe"] = _customrecipe

_customwebapp = types.ModuleType("dataiku.customwebapp")


def get_webapp_resource() -> str | None:
    return None


_customwebapp.get_webapp_resource = get_webapp_resource
sys.modules["dataiku.customwebapp"] = _customwebapp

runnables = _runnables
customrecipe = _customrecipe
customwebapp = _customwebapp
