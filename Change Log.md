# Change Log

- Dashboard DuckDB reloads now fail explicitly when required user-activity GOLD tables are missing or fail to materialize.
- Dashboard debug reload failures now surface the failed table and underlying load error in the top-level response.
- Dashboard DuckDB reloads now bound raw development-activity event loading to recent partitions by default while preserving full daily facts.
- Dashboard webapp startup freshness checks now resolve the active project-specific DuckDB path before evaluating or deleting stale local databases.
- Dashboard webapp startup now checks local DuckDB freshness during both DSS-injected and standalone backend registration, using successful rebuild metadata before falling back to file mtime.
- GOLD fact exports now use manifest watermarks from `gold/_state/manifest.json` with the configured lookback window, avoiding full-history re-exports during incremental runs while preserving full exports when incremental mode is disabled or no usable watermark exists.
- Added detailed GOLD recipe phase timing to diagnose long-running fact and product builds.
- Improved dashboard DuckDB reloads by bulk-loading special daily fact tables and adding separate load/count timing logs.
