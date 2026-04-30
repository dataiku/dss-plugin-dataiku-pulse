# Pulse dashboard: key configuration references

This page documents the **authoritative configuration files** that control:

- which DSS objects are treated as **Assets** vs **Products** in the dashboard
- how those objects are indexed in DuckDB views
- how **Development Activity** categories roll up into **Capabilities**

These configs are **plugin-owned** (not intended to be user/customer settings).

---

## 1) Asset/Product taxonomy (source of truth)

**File**
- `python-lib/pulse_dashboard/configs/terminology.yaml`

**Purpose**
- Defines the canonical lists of object types:
  - `assets`: objects shown in the Assets catalog
  - `products`: objects shown in the Products catalog

**Important constraints**
- The names in this file must match the values used downstream:
  - For products activity, they must match `object_type` values in the activity events stream.
  - For asset/product indexes, they must match the `object_type`/`product_type` strings used in the generated index views.

**What it affects**
- DuckDB views generated at dashboard startup:
  - `base_asset_index`
  - `base_product_index` (only when the registry path is not used)
  - `product_activity_30d` (product types filter list)

**Where it’s implemented**
- Config loader + SQL generator:
  - `python-lib/pulse_dashboard/pulse_duckdb/engine/config_driven_views.py`
- View build orchestration:
  - `python-lib/pulse_dashboard/pulse_duckdb/engine/view_builder.py`

---

## 2) Asset indexing schema (table/column mapping)

**File**
- `python-lib/pulse_dashboard/configs/asset_structure.yaml`

**Purpose**
- For each asset type listed in `terminology.yaml:assets`, defines:
  - which base inventory table/view to read from (`table`)
  - how to build the standardized index columns

**Output view**
- `base_asset_index` with a stable schema:
  - `instance_name`, `project_key`, `object_type`, `object_key`, `object_name`, `object_subtype`,
    `owner_login`, `last_modified_by_login`, `created_at`, `updated_at`

**Expression rules**
- Scalar string value: treated as a column name (identifier)
- YAML list: treated as `COALESCE(col1, col2, ...)`
- YAML `null`: treated as SQL `NULL`

**Best-effort behavior**
- If the mapped `table` does not exist in DuckDB, the branch is skipped.
- If required columns are missing, the branch is skipped.
- Skips are logged from `config_driven_views.py`.

---

## 3) Product indexing schema (table/column mapping)

**File**
- `python-lib/pulse_dashboard/configs/product_structure.yaml`

**Purpose**
- For each product type listed in `terminology.yaml:products`, defines:
  - which base product inventory table/view to read from (`table`)
  - how to build the standardized product index columns

**Output view**
- `base_product_index` with a stable schema:
  - `instance_name`, `project_key`, `product_type`, `product_key`, `product_name`, `product_subtype`,
    `owner_login`, `last_modified_by_login`, `created_at`, `updated_at`

**Important: registry precedence**
Many deployments build products from a GOLD-generated registry table.

- If `base_dataiku_products_registry` exists, the dashboard will generate `base_product_index` from it.
- When that registry path succeeds, the config-driven `base_product_index` generation is skipped to avoid overwriting the registry-built view.

**Where it’s implemented**
- Registry path:
  - `python-lib/pulse_dashboard/pulse_duckdb/engine/view_builder.py` (dynamic `base_product_index` generator)
- Config-driven fallback:
  - `python-lib/pulse_dashboard/pulse_duckdb/engine/config_driven_views.py`

---

## 4) Development Activity: category → capability taxonomy

**File**
- `python-lib/data_collection/pulse_duckdb/gold_specs/dataiku_dev_tools/category_to_capability.yaml`

**Purpose**
- Defines the mapping from curated `dataiku_category` values to a high-level `capability`.
- Provides UI-friendly labels via:
  - `capability_display_name`
  - `category_display_name`

**Where it ends up**
- Materialized as the DuckDB table:
  - `dim_category_to_capability`

**How it’s used**
- Dev activity UI depends on:
  - `fact_dev_activity_events` (event stream)
  - `dim_category_to_capability` (taxonomy)
- The dashboard creates a UI-ready stream:
  - `final_build_development_activity_events`

**Matching rule (important)**
- `dataiku_category` in events and the taxonomy may differ in case/format depending on source.
- The dashboard view join is case-insensitive (`lower(trim(...))`) to be resilient.

**Where it’s built**
- GOLD table build recipe:
  - `custom-recipes/create-gold-tables/recipe.py` (`_build_dim_category_to_capability`)

---

## Common change workflows

### A) Move an object type between Assets and Products
1. Edit `python-lib/pulse_dashboard/configs/terminology.yaml`.
2. Ensure the object type exists in the corresponding structure file:
   - `asset_structure.yaml` (if moved to assets)
   - `product_structure.yaml` (if moved to products)
3. Reload DuckDB views:
   - In DSS webapp: use `POST /api/debug/duckdb/reload`
   - Or delete the DuckDB file and restart the backend.

### B) Add a new dev-activity category / capability mapping
1. Update `python-lib/data_collection/pulse_duckdb/gold_specs/dataiku_dev_tools/category_to_capability.yaml`.
2. Rebuild GOLD tables (recipe) so `dim_category_to_capability` is updated.
3. Reload DuckDB in the dashboard.

---

## Additional references (often needed)

### A) Dev Activity category assignment (audit msgType → dataiku_category)

**Files**
- `python-lib/data_collection/audit_logs_modules/event_mapping.py`
- `python-lib/data_collection/audit_logs_modules/mapping.csv`

**Purpose**
- Controls how raw DSS audit log message types (`message_msgType`) get mapped into a curated
  `dataiku_category`.
- This is upstream of the capability taxonomy.

**Important constraints**
- `event_mapping.py` normalizes mapped categories to lowercase.
- If you add new audit msgTypes, the usual change is adding a new row in `mapping.csv`.

### B) Product registry definitions (GOLD registry → base_product_index)

**Directory**
- `python-lib/data_collection/pulse_duckdb/gold_specs/dataiku_products/`

**Purpose**
- Defines how certain DSS objects are registered as “products” in GOLD via
  `base_dataiku_products_registry`.
- Many deployments use this registry to build the dashboard’s `base_product_index`.

**Typical edits**
- Add a new product type or adjust its source-table column mapping.

---

## Notes / diagnostics

- Unmapped dev categories can be inspected via:
  - `dev_activity_unmapped_categories_30d`
- Config alignment warnings:
  - `config_driven_views.validate_configs()` logs missing/unused mappings at startup.
