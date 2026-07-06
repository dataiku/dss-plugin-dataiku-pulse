# Pulse Data Product Inventory (source-traced)

> Purpose: complete inventory of data products Pulse produces, each traced to its raw source, to plan a from-scratch rewrite.
> Plugin root: `dss-plugin-dataiku-pulse/`

## Pipeline overview
RAW (DSS API `list_*` methods + audit logs) → SILVER (partitioned parquet `partitioned_data/silver/category=<cat>/module=<mod>/instance_name=…/year=…/month=…/day=…`) → GOLD (parquet in `gold/` managed folder, built by `custom-recipes/create-gold-tables/recipe.py`) → DASHBOARD (DuckDB loads GOLD parquet, builds compatibility + final views; served by `python-lib/pulse_dashboard/webapp_backend/full_backend.py`).

Key files:
- Gold build: `custom-recipes/create-gold-tables/recipe.py`; specs `python-lib/data_collection/pulse_duckdb/gold_specs/{instance,project,dataiku_products,dataiku_dev_tools,object_activity}/`
- Collection rules: `python-lib/data_collection/collection_exclusions/*.yaml`; engine `data_collection/method_rules.py`, `exclusion_config.py`, `data_collection/introspection.py`
- Runnables: `python-runnables/{data-gather-instance,data-gather-project,data-gather-audit-logs,load-event-server-history,reload-silver-layer}/runnable.py`
- Audit processors: `python-lib/data_collection/audit_logs_modules/{event_mapping.py,users.py,mapping.csv,users_activity_vocab.yaml,modules.yaml,audit_paths.py}`
- Dashboard engine: `python-lib/pulse_dashboard/pulse_duckdb/engine/{init_db.py,gold_loader.py,view_builder.py,config_driven_views.py}`; specs `.../datasets/{base,views}/*.yaml`; configs `python-lib/pulse_dashboard/configs/{asset_structure,product_structure,terminology}.yaml`

---

## A. GOLD TABLES

Built by `create-gold-tables/recipe.py`. All `base_*/dim_*/fact_*` tables unload to `gold/<name>.parquet`; event facts partitioned to `gold/<name>/instance_name=…/year=…/month=…/day=…`.

### A.1 YAML-spec tables (`gold_specs/`, template `latest_by_partition` = newest row per partition key)

| GOLD TABLE | GRAIN | SILVER SOURCE (category/module) |
|---|---|---|
| base_apps_instance_metadata_history | latest app per (instance, appid) | apps / instance_metadata |
| base_clusters_instance_metadata_history | latest cluster per instance | clusters / instance_metadata |
| base_code_envs_instance_metadata_history | latest code env | code_envs / instance_metadata |
| base_code_env_usages_instance_metadata_history | latest code-env usage | code_env_usages / instance_metadata |
| base_groups_instance_metadata_history | latest group | groups / instance_metadata |
| base_llms_instance_metadata_history | latest instance LLM | llms / instance_metadata |
| base_plugins_instance_metadata_history | latest plugin | plugins / instance_metadata |
| base_projects_instance_metadata_history | latest project (instance view) | projects / instance_metadata |
| base_users_instance_metadata_history | latest user row | users / instance_metadata |
| base_workspaces_instance_metadata_history | latest workspace | workspaces / instance_metadata |
| base_license_status_latest | latest license status/instance | license / license_status |
| base_license_max_licenses_latest | max caps per (instance, profile) | license / max_licenses |
| base_license_addon_licenses_latest | addon on/off per (instance, addon_key) | license / addon_licenses |
| base_license_limits_wide_latest | caps pivoted wide (1 row/instance); cols injected from `instance/license_profiles.yaml` via `_inject_wide_license_sql` | base_license_status_latest + base_license_max_licenses_latest |
| base_datasets_project_metadata_history | latest dataset per (instance, project, name) | datasets / project_metadata |
| base_recipes_project_metadata_history | latest recipe | recipes / project_metadata |
| base_scenarios_project_metadata_history | latest scenario | scenarios / project_metadata |
| base_analyses_project_metadata_history | latest visual analysis | analyses / project_metadata |
| base_managed_folders_project_metadata_history | latest managed folder | managed_folders / project_metadata |
| base_sql_notebooks_project_metadata_history | latest SQL notebook | sql_notebooks / project_metadata |
| base_knowledge_banks_project_metadata_history | latest knowledge bank | knowledge_banks / project_metadata |
| base_saved_models_project_metadata_history | latest saved model | saved_models / project_metadata |
| base_retrieval_augmented_llms_project_metadata_history | latest RAG LLM | retrieval_augmented_llms / project_metadata |
| base_api_services_project_metadata_history | latest API service | api_services / project_metadata |
| base_agent_tools_project_metadata_history | latest agent tool | agent_tools / project_metadata |
| base_dashboards_project_metadata_history | latest dashboard | dashboards / project_metadata |
| base_insights_project_metadata_history | latest insight | insights / project_metadata |
| base_webapps_project_metadata_history | latest webapp | webapps / project_metadata |

### A.2 Embedded-SQL tables (functions in `recipe.py`)

| GOLD TABLE | GRAIN | SILVER / CONFIG SOURCE |
|---|---|---|
| fact_dev_activity_events (`_build_fact_dev_activity_events`) | one row per audit event for curated dev modules | `event_mapping/module=<m>` for `dataiku_dev_tools/toolbox.yaml` = {datasets, visual_recipes, coding, statistic_analytics, mlops, genai_llm} |
| fact_object_activity_events (`_build_fact_object_activity_events`) | one row per object-level event; object_key/type parsed from callpath/extras per module; also compat view `base_object_activity_events` | `event_mapping/module=<m>` for `object_activity/toolbox.yaml` = {datasets, visual_recipes, apis, webapps, charts_dashboard, application_designer}; ⋈ dim_category_to_capability |
| fact_user_activity_daily (`_build_fact_user_activity_daily`) | one row per (day, instance, login_norm); viewing/developing counts | `users/user_activity` (hourly) rolled up |
| fact_user_activity_project_daily (`_build_fact_user_activity_project_daily`) | one row per (day, instance, login_norm, project_key) | `users/user_activity` |
| dim_category_to_capability (`_build_dim_category_to_capability`) | one row per dataiku_category→capability | `dataiku_dev_tools/category_to_capability.yaml` (drops `uncategorized`) |
| dim_addon_feature_flags (`_build_dim_addon_feature_flags`) | one row per addon_key, enabled_any_instance | base_license_addon_licenses_latest |
| base_dataiku_products_registry (inline in `run()`) | one row per product_type → source-table/column mapping (static) | `gold_specs/dataiku_products/*.yaml` = {agent_tool, api_service, dashboard, dataiku_application, insight, retrieval_augmented_llm, saved_model, web_application} |

---

## B. SILVER MODULES and RAW SOURCES

Engine: `introspection.get_noarg_list_methods` collects every no-arg `list_*`; minus exclusions; plus per-method rule overrides (`method_rules.py`).

### B.1 Instance — `python-runnables/data-gather-instance/runnable.py`
- Auto-collects ALL `client.list_*()`. `instance_data.yaml` exclusions = **empty** (nothing disabled).
- `instance_method_rules.yaml`: list_connections (python_hook drops secrets), list_imported_bundles, list_ml_tasks, list_llms, get_licensing_status = enabled; **list_global_api_keys = DISABLED (secrets)**.
- Curated modules → `silver/category=<X>/module=instance_metadata`: apps, clusters, code_envs, code_env_usages, connections, groups, llms, plugins, projects, users, workspaces (from matching `client.list_*`).
- LICENSE (special): `get_custom_instance_methods` → **`client.get_licensing_status()`** (NOT get_instance_info / get_license). `collect_licensing_output` → 3 silver modules `category=license`: license_status, max_licenses (+`SUBLICENSE_*`), addon_licenses; raw JSON archived.
- `get_instance_info()` used ONLY for instance_name (nodeId/installId) + audit dataDir; not a collected module.
- Project-invariant inclusions run once vs worker project (`instance_project_inclusion.yaml`): list_imported_bundles, list_llms, list_ml_tasks.
- Dormant/not-curated (TODO_ instance flatten configs): code_studio_templates, data_collections, futures, global_api_keys, groups_info, imported_bundles, logs, messaging_channels, ml_tasks, project_keys, projects, running_notebooks, running_scenarios, users_activity, users_info. Flatten configs instance_info/footprint_*/name_mapping/users_git_history exist but have NO active collector (legacy/inert).

### B.2 Project — `python-runnables/data-gather-project/runnable.py` → `collect_all_projects` → `collect_project_list_methods`
- Selects projects changed since `projects_delta` cursor via `client.list_projects()` (versionTag/creationTag.lastModifiedOn), then auto-collects ALL `project.list_*()`.
- `projects_data.yaml` / `project_method_rules.yaml` DISABLE: list_imported_bundles, list_llms, list_macros, list_ml_tasks, list_running_notebooks.
- Curated modules → `silver/category=<X>/module=project_metadata`: datasets, recipes, scenarios, analyses, managed_folders, sql_notebooks, knowledge_banks, saved_models, retrieval_augmented_llms, api_services, agent_tools, agents, dashboards, insights, llms, projects, users, webapps.
- Dormant (TODO_ project flatten configs): code_studios, evaluation_stores, exported_bundles, imported_bundles, jobs, jupyter_notebooks, macros, ml_tasks, mltask_queues, model_evaluation_stores, plugins_usages, running_notebooks.

### B.3 Audit — `python-runnables/data-gather-audit-logs/runnable.py` (+ `load-event-server-history/runnable.py`)
- RAW: `audit_paths.resolve_audit_logs_dir` = `client.get_instance_info().raw["dataDirPath"] + /run/audit/`, files `audit.log*` (JSONL). Cached mode `PULSE_AUDIT_LOGS_USE_CACHED` → `../audit_data/`. Filter `topic=="generic"`, incremental via `audit_log_delta` cursor.
- Processors (`modules.yaml` = [event_mapping, users]):

| PROCESSOR | SILVER OUTPUT | GRAIN | LOGIC |
|---|---|---|---|
| event_mapping (`event_mapping.py`) | `category=event_mapping/module=<dataiku_category>` + flattened `audit_dataiku_usage/audit_metadata` variants | 1 row per audit event, +`dataiku_category`, +project_key/webapp_id from `authvia` | join `message_msgType`→`mapping.csv`; drop rows mapped `DROP_DELETE` |
| users (`users.py`) | `category=users/module=user_activity` | hourly per (instance, login, project_key): viewing/developing counts | keep authSource=USER_FROM_UI, drop scenario/job rows; developing = msgtypebase∈mutating OR msgType∈action_words & ∉remove_words (`users_activity_vocab.yaml`) |

---

## C. DASHBOARD REPORTS / VIEWS

Load: `init_db.ensure_database_ready` loads GOLD parquet (`base_/dim_/fact_/reg_`) into DuckDB, then `_maybe_create_inventory_views` (maps `*_metadata_history`→`base_*_metadata`, and `base_object_activity_events`=fact_object_activity_events), `_maybe_create_license_views` (`base_license_*_latest`), then `view_builder.build_views_from_specs`.

### C.1 Views

| VIEW | GRAIN | SOURCE |
|---|---|---|
| base_asset_index | union of asset types → uniform row; from `configs/asset_structure.yaml` + terminology assets {analysis, project, dataset, knowledge_bank, managed_folder, recipe, scenario, sql_notebook} (config_driven_views); YAML fallback unions projects/datasets/recipes/scenarios history | asset base_* metadata |
| base_product_index | union of product types; from `configs/product_structure.yaml` + terminology products {agent_tool, api_service, api_endpoint, agent, dashboard, insight, retrieval_augmented_llm, saved_model, web_application, dataiku_application} | product base_* metadata |
| v_object_activity_events | passthrough | base_object_activity_events (=fact_object_activity_events) |
| asset_activity_30d | per-asset 30d activity/active users/last_activity | v_object_activity_events |
| product_activity_30d | per-product 30d activity/active users/last_activity | v_object_activity_events |
| final_build_catalog | Build→Catalog: 1 row/asset + 30d activity + completeness | base_asset_index ⋈ asset_activity_30d |
| final_build_products_catalog | Build→Products: 1 row/product + activity + completeness | base_product_index (⋈ base_projects_metadata) |
| final_build_products_metrics | product counts by type | final_build_products_catalog |
| final_build_development_activity_events | UI-ready dev event stream | fact_dev_activity_events ⋈ dim_category_to_capability |
| dev_activity_capability_daily / _capability_30d / _category_30d / _tag_30d / _top_users_30d | dev rollups | final_build_development_activity_events |
| dev_activity_unmapped_categories_30d | categories missing capability mapping | fact_dev_activity_events |
| final_users_directory | canonical cross-instance user directory (newest run_ts/login_norm; hub tiebreak) | base_users_instance_metadata_history |

Dashboard `datasets/base/*.yaml` are empty schema-seed tables (base_agents_metadata, base_api_endpoints_metadata, base_dashboards_metadata, base_dataiku_applications_metadata, base_datasets_metadata, base_groups, base_instance_registry, base_mlmodels_metadata, base_projects_metadata, base_recipes_metadata, base_scenarios_metadata, base_user_group_membership, base_users, base_webapps_metadata, dim_category_to_capability, fact_dev_activity_events, fact_object_activity_events) — real rows arrive via GOLD load / compatibility views.

### C.2 Backend endpoints (`full_backend.py`, ~40 routes) by report area

| AREA | ENDPOINTS | TABLES/VIEWS |
|---|---|---|
| Startup/infra | /api/status, /api/startup/{flags,duckdb,status,init-status}, /__ping, static | metadata; expected: final_build_catalog, final_build_products_catalog, dev_activity_capability_daily, final_build_development_activity_events |
| Debug (gated by standard.debug) | /api/duckdb/query, /api/debug/duckdb/{reload,tables,table/<t>} | arbitrary / information_schema |
| Asset Catalog | /api/build/assets, /assets/{facets,details,metadata-summary} | final_build_catalog; `_OBJECT_EXTRAS_SOURCES`→base_*_metadata_history; v_object_activity_events |
| Product Catalog | /api/build/products, /products/{facets,details,metadata-summary,type-metrics} | final_build_products_catalog; v_object_activity_events |
| Consumption/Usage | /api/consumption/products/{facets,details,summary,lifecycle-summary}, /process-usage, /process-usage/capability/<cap> | v_object_activity_events, base_product_index, final_build_products_catalog |
| Dev Activity | /api/build/development-activity, /development-activity/capability/<cap>, /development-activity/user/<login> | final_build_development_activity_events, dev_activity_capability_daily, dev_activity_capability_30d, dev_activity_category_30d, dev_activity_top_users_30d |
| User Directory + License Utilization | /api/build/users/{facets,kpis,active-monthly,leaderboard,creator-risk,segments,stickiness}, /users/<login>, /users/<login>/top-projects | final_users_directory, base_users_instance_metadata_history, fact_user_activity_daily, fact_user_activity_project_daily, base_license_status_latest, base_license_max_licenses_latest, base_license_addon_licenses_latest (license utilization served inside /users/kpis; license groups from configs/terminology.yaml) |

Notes:
- No standalone /api/license or /api/instance-inventory route. License utilization rides on /api/build/users/kpis.
- Instance-inventory GOLD tables (apps/clusters/code_envs/plugins/workspaces/llms history, base_instance_registry, base_mlmodels_metadata, base_groups, base_user_group_membership) are loaded but several lack a dedicated endpoint — rewrite backlog candidates.
- terminology.yaml license_groups: creator={FULL_DESIGNER,DATA_DESIGNER,ADVANCED_ANALYTICS_DESIGNER}, consumer={AI_CONSUMER,READER}, admin={TECHNICAL_ACCOUNT}.
