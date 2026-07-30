from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def _load_recipe_module(repo_root: Path):
    recipe_path = repo_root / "custom-recipes" / "create-gold-tables" / "recipe.py"
    spec = importlib.util.spec_from_file_location("pulse_create_gold_recipe", recipe_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load recipe module from {recipe_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    os.environ.setdefault("PULSE_DUCKDB_READ_ONLY", "0")

    from data_collection.pulse_duckdb.context import build_storage_context
    from pulse_dashboard import settings as pulse_settings
    from pulse_dashboard.pulse_duckdb.engine import create_connection
    from pulse_dashboard.pulse_duckdb.engine.config_driven_views import build_base_product_index, build_product_activity_30d
    from pulse_dashboard.pulse_duckdb.engine.view_builder import build_views_from_specs

    recipe = _load_recipe_module(repo_root)

    source_project_key = pulse_settings.PULSE_SOURCE_PROJECT_KEY
    ctx = build_storage_context(project_key=source_project_key, folder_lookup="partitioned_data")
    gold_builder_module_path = Path(recipe.__file__).resolve().parent
    base_dir = gold_builder_module_path.parent.parent / "python-lib" / "data_collection" / "pulse_duckdb" / "gold_specs"

    conn = create_connection(read_only=False)
    try:
        recipe._build_dim_category_to_capability(conn, base_dir=base_dir)
        built = recipe._build_fact_object_activity_events(conn, ctx=ctx, base_dir=base_dir)
        if not built:
            raise RuntimeError("Object activity rebuild produced no table; no event-mapping modules were available")

        build_product_activity_30d(conn)
        build_base_product_index(conn)
        build_views_from_specs(conn)

        top_rows = conn.execute(
            """
            SELECT object_key, COUNT(*) AS events
            FROM fact_object_activity_events
            WHERE object_type = 'web_application'
            GROUP BY 1
            ORDER BY events DESC, object_key
            LIMIT 20;
            """.strip()
        ).fetchall()
        print(f"Rebuilt object activity in {pulse_settings.DUCKDB_PATH}")
        print("Top web_application object_key values after rebuild:")
        for object_key, events in top_rows:
            print(f"- {object_key!r}: {events}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
