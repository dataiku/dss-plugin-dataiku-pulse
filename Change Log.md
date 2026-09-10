# Change Log

- GOLD fact exports now use manifest watermarks from `gold/_state/manifest.json` with the configured lookback window, avoiding full-history re-exports during incremental runs while preserving full exports when incremental mode is disabled or no usable watermark exists.
- Added detailed GOLD recipe phase timing to diagnose long-running fact and product builds.
- Improved dashboard DuckDB reloads by bulk-loading special daily fact tables and adding separate load/count timing logs.
