from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


RECIPE_PATH = Path('custom-recipes/create-gold-tables/recipe.py')
RECIPE_JSON_PATH = Path('custom-recipes/create-gold-tables/recipe.json')


def _load_recipe_module():
    sys.path.insert(0, str(Path('tests/stubs').resolve()))
    sys.path.insert(0, str(Path('python-lib').resolve()))
    spec = importlib.util.spec_from_file_location('create_gold_recipe_test_module', RECIPE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_recipe_json_no_longer_exposes_activity_fact_toggles():
    payload = json.loads(RECIPE_JSON_PATH.read_text(encoding='utf-8'))
    param_names = [param.get('name') for param in payload.get('params', [])]

    assert 'build_dev_activity' not in param_names
    assert 'build_object_activity' not in param_names


@pytest.fixture()
def recipe_module():
    return _load_recipe_module()


def test_recipe_builds_required_activity_facts_unconditionally(monkeypatch, recipe_module):
    calls: list[str] = []
    events: list[str] = []

    class _Conn:
        def execute(self, sql, *_args, **_kwargs):
            events.append(sql)

            class _Result:
                def fetchone(self):
                    if sql == "SELECT current_setting('memory_limit')":
                        return ("4.0 GiB",)
                    if sql == "SELECT current_setting('threads')":
                        return (2,)
                    return (None,)
            return _Result()

    class _Setup:
        conn = _Conn()

    class _Ctx:
        folder_lookup = 'stub'
        connection_type = 'EC2'
        connection_name = 'stub-conn'

    monkeypatch.setattr(recipe_module.dataiku, 'default_project_key', lambda: 'TEST_PROJECT')
    monkeypatch.setattr(recipe_module, 'resolve_gold_folder_lookup', lambda: 'gold_data')
    monkeypatch.setattr(recipe_module, 'get_recipe_config', lambda: {
        'unload_behavior': 'dataiku',
        'incremental_enabled': True,
        'lookback_days': 3,
        'build_dev_activity': False,
        'build_object_activity': False,
    })
    monkeypatch.setattr(recipe_module, 'ensure_managed_folder', lambda **_kwargs: None)
    monkeypatch.setattr(recipe_module, 'build_storage_context', lambda **_kwargs: _Ctx())
    monkeypatch.setattr(recipe_module, 'prepare_duckdb', lambda **_kwargs: events.append('prepare_duckdb') or _Setup())
    monkeypatch.setattr(recipe_module, 'effective_memory_limit_bytes', lambda: (10 * 1024**3, 'cgroup_v2'))
    monkeypatch.setattr(recipe_module, 'configure_storage', lambda *_args, **_kwargs: events.append('configure_storage'))
    monkeypatch.setattr(recipe_module, 'resolve_unique_db_path', lambda **_kwargs: '/tmp/test.duckdb')
    monkeypatch.setattr(recipe_module, 'resolve_gold_spec_build_order', lambda *args, **kwargs: [])
    monkeypatch.setattr(recipe_module, 'apply_gold_spec', lambda *_args, **_kwargs: events.append('apply_gold_spec'))
    monkeypatch.setattr(recipe_module, 'create_silver_view', lambda **_kwargs: events.append('create_silver_view') or ('stub_view', None))
    monkeypatch.setattr(recipe_module, 'build_license_wide_sql_params', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(recipe_module, 'build_dim_addon_feature_flags', lambda *_args, **_kwargs: 'dim_addon_feature_flags')
    monkeypatch.setattr(recipe_module, 'build_dim_category_to_capability', lambda *_args, **_kwargs: events.append('build_dim_category_to_capability') or calls.append('dim_category_to_capability') or 'dim_category_to_capability')
    monkeypatch.setattr(recipe_module, 'build_dim_dev_activity_event_classification', lambda *_args, **_kwargs: events.append('build_dim_dev_activity_event_classification') or calls.append('dim_dev_activity_event_classification') or 'dim_dev_activity_event_classification')
    monkeypatch.setattr(recipe_module, 'build_fact_dev_activity_events', lambda *_args, **_kwargs: events.append('build_fact_dev_activity_events') or calls.append('fact_dev_activity_events') or 'fact_dev_activity_events')
    monkeypatch.setattr(recipe_module, 'build_fact_user_activity_daily', lambda *_args, **_kwargs: 'fact_user_activity_daily')
    monkeypatch.setattr(recipe_module, 'build_fact_user_activity_project_daily', lambda *_args, **_kwargs: 'fact_user_activity_project_daily')
    monkeypatch.setattr(recipe_module, 'build_fact_formal_mau_daily', lambda *_args, **_kwargs: 'fact_formal_mau_daily')
    monkeypatch.setattr(recipe_module, 'build_fact_license_utilization_daily', lambda *_args, **_kwargs: 'fact_license_utilization_daily')
    monkeypatch.setattr(recipe_module, 'collect_user_activity_quality_report', lambda *_args, **_kwargs: {'ok': True})
    monkeypatch.setattr(recipe_module, 'collect_license_utilization_quality_report', lambda *_args, **_kwargs: {'ok': True})
    monkeypatch.setattr(recipe_module, 'build_fact_object_activity_events', lambda *_args, **_kwargs: events.append('build_fact_object_activity_events') or calls.append('fact_object_activity_events') or 'fact_object_activity_events')
    monkeypatch.setattr(recipe_module, 'build_base_dataiku_products_registry', lambda *_args, **_kwargs: 'base_dataiku_products_registry')
    monkeypatch.setattr(recipe_module, 'read_manifest', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(recipe_module, 'set_manifest_watermark', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recipe_module, 'stamp_manifest_updated_at', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recipe_module, 'write_manifest', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recipe_module, 'list_table_names', lambda *_args, **_kwargs: ['fact_dev_activity_events', 'fact_object_activity_events'])
    monkeypatch.setattr(recipe_module, 'group_gold_tables_by_prefix', lambda names: {
        'base_tables': [],
        'dim_tables': [],
        'agg_tables': [],
        'fact_tables': list(names),
    })
    monkeypatch.setattr(recipe_module, 'gold_destination_for_table', lambda name: f'gold/{name}')
    monkeypatch.setattr(recipe_module, 'log_pre_unload_debug', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recipe_module, 'load_dev_toolbox_modules', lambda *_args, **_kwargs: ['module_a'])
    monkeypatch.setattr(recipe_module, 'load_object_activity_modules', lambda *_args, **_kwargs: ['module_b'])
    monkeypatch.setattr(recipe_module, '_create_event_mapping_module_view', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recipe_module, 'unload_gold_tables', lambda *_args, **_kwargs: (['fact_dev_activity_events', 'fact_object_activity_events'], []))

    result = recipe_module.run()

    assert events.count("SET memory_limit = '4294967296B'") == 1
    assert events.count('SET threads = 2') == 1
    assert events.index('prepare_duckdb') < events.index("SET memory_limit = '4294967296B'")
    assert events.index('SET threads = 2') < events.index('configure_storage')
    assert events.index('SET threads = 2') < events.index('build_dim_category_to_capability')
    assert events.index('SET threads = 2') < events.index('build_fact_dev_activity_events')
    assert events.index('SET threads = 2') < events.index('build_fact_object_activity_events')
    assert 'fact_dev_activity_events' in calls
    assert 'fact_object_activity_events' in calls
    assert result['source_project_key'] == 'TEST_PROJECT'
    assert result['duckdb_connection_ready'] is True
    assert result['unload_behavior'] == 'dataiku'
    assert result['built_dev_activity'] == [
        'dim_category_to_capability',
        'dim_dev_activity_event_classification',
        'fact_dev_activity_events',
    ]
    assert result['built_object_activity'] == ['fact_object_activity_events']
    assert 'build_dev_activity' not in result
    assert 'build_object_activity' not in result
