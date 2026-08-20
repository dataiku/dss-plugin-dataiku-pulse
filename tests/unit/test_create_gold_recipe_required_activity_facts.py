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

    class _Conn:
        def execute(self, *_args, **_kwargs):
            class _Result:
                def fetchone(self):
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
    monkeypatch.setattr(recipe_module, 'prepare_duckdb', lambda **_kwargs: _Setup())
    monkeypatch.setattr(recipe_module, 'configure_storage', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recipe_module, 'resolve_unique_db_path', lambda **_kwargs: '/tmp/test.duckdb')
    monkeypatch.setattr(recipe_module, 'resolve_gold_spec_build_order', lambda *args, **kwargs: [])
    monkeypatch.setattr(recipe_module, 'apply_gold_spec', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(recipe_module, 'create_silver_view', lambda **_kwargs: ('stub_view', None))
    monkeypatch.setattr(recipe_module, 'build_license_wide_sql_params', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(recipe_module, 'build_dim_addon_feature_flags', lambda *_args, **_kwargs: 'dim_addon_feature_flags')
    monkeypatch.setattr(recipe_module, 'build_dim_category_to_capability', lambda *_args, **_kwargs: calls.append('dim_category_to_capability') or 'dim_category_to_capability')
    monkeypatch.setattr(recipe_module, 'build_dim_dev_activity_event_classification', lambda *_args, **_kwargs: calls.append('dim_dev_activity_event_classification') or 'dim_dev_activity_event_classification')
    monkeypatch.setattr(recipe_module, 'build_fact_dev_activity_events', lambda *_args, **_kwargs: calls.append('fact_dev_activity_events') or 'fact_dev_activity_events')
    monkeypatch.setattr(recipe_module, 'build_fact_user_activity_daily', lambda *_args, **_kwargs: 'fact_user_activity_daily')
    monkeypatch.setattr(recipe_module, 'build_fact_user_activity_project_daily', lambda *_args, **_kwargs: 'fact_user_activity_project_daily')
    monkeypatch.setattr(recipe_module, 'build_fact_formal_mau_daily', lambda *_args, **_kwargs: 'fact_formal_mau_daily')
    monkeypatch.setattr(recipe_module, 'build_fact_license_utilization_daily', lambda *_args, **_kwargs: 'fact_license_utilization_daily')
    monkeypatch.setattr(recipe_module, 'collect_user_activity_quality_report', lambda *_args, **_kwargs: {'ok': True})
    monkeypatch.setattr(recipe_module, 'collect_license_utilization_quality_report', lambda *_args, **_kwargs: {'ok': True})
    monkeypatch.setattr(recipe_module, 'build_fact_object_activity_events', lambda *_args, **_kwargs: calls.append('fact_object_activity_events') or 'fact_object_activity_events')
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

    assert 'fact_dev_activity_events' in calls
    assert 'fact_object_activity_events' in calls
    assert result['built_dev_activity'] == [
        'dim_category_to_capability',
        'dim_dev_activity_event_classification',
        'fact_dev_activity_events',
    ]
    assert result['built_object_activity'] == ['fact_object_activity_events']
    assert 'build_dev_activity' not in result
    assert 'build_object_activity' not in result
