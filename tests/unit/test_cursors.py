from __future__ import annotations

import pandas as pd

from data_collection.helper.cursors import CursorSpec, resolve_cursor_ts, update_cursor_ts


class FakeProject:
    def __init__(self, variables=None, raise_on_set=False):
        self._variables = variables if variables is not None else {"local": {}}
        self.raise_on_set = raise_on_set
        self.set_calls = []

    def get_variables(self):
        return self._variables

    def set_variables(self, variables):
        if self.raise_on_set:
            raise RuntimeError("boom: could not write variables")
        self.set_calls.append(variables)
        self._variables = variables


class FakeClient:
    def __init__(self, project):
        self._project = project

    def get_project(self, project_key):
        return self._project


def test_update_cursor_ts_returns_true_on_success():
    project = FakeProject(variables={"local": {}})
    client = FakeClient(project)
    spec = CursorSpec(variable_name="my_cursor")

    result = update_cursor_ts(
        client=client, project_key="PROJ", spec=spec, value="2026-01-01T00:00:00+00:00"
    )

    assert result is True
    assert project.set_calls
    assert project.set_calls[-1]["local"]["my_cursor"] == "2026-01-01T00:00:00+00:00"


def test_update_cursor_ts_returns_false_when_client_raises():
    project = FakeProject(variables={"local": {}}, raise_on_set=True)
    client = FakeClient(project)
    spec = CursorSpec(variable_name="my_cursor")

    result = update_cursor_ts(
        client=client, project_key="PROJ", spec=spec, value="2026-01-01T00:00:00+00:00"
    )

    assert result is False


def test_resolve_cursor_ts_local_mode_returns_default():
    default_ts = pd.Timestamp("2020-01-01T00:00:00+00:00")
    spec = CursorSpec(variable_name="my_cursor")

    result = resolve_cursor_ts(
        client=None,
        project_key="PROJ",
        param_set={},
        spec=spec,
        default_ts=default_ts,
        local_mode=True,
    )

    assert result == default_ts


def test_resolve_cursor_ts_debug_key_truthy_returns_default():
    default_ts = pd.Timestamp("2020-01-01T00:00:00+00:00")
    spec = CursorSpec(variable_name="my_cursor", debug_key="force_default")

    result = resolve_cursor_ts(
        client=None,
        project_key="PROJ",
        param_set={"force_default": True},
        spec=spec,
        default_ts=default_ts,
        local_mode=False,
    )

    assert result == default_ts


def test_resolve_cursor_ts_project_variable_wins():
    default_ts = pd.Timestamp("2020-01-01T00:00:00+00:00")
    project = FakeProject(
        variables={"local": {"my_cursor": "2026-01-02T00:00:00+00:00"}}
    )
    client = FakeClient(project)
    spec = CursorSpec(variable_name="my_cursor")

    result = resolve_cursor_ts(
        client=client,
        project_key="PROJ",
        param_set={},
        spec=spec,
        default_ts=default_ts,
        local_mode=False,
    )

    assert result == pd.Timestamp("2026-01-02T00:00:00+00:00")
