# Change Log

- GOLD fact exports now use manifest watermarks from `gold/_state/manifest.json` with the configured lookback window, avoiding full-history re-exports during incremental runs while preserving full exports when incremental mode is disabled or no usable watermark exists.
