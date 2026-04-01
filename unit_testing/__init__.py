"""Test entrypoint and manual experiments.

Run from repo root (`project-lib-versioned/python/dataiku-pulse`) using the project code env:

  /opt/dataiku/python-code-envs/data_collection/bin/python \
    unit_testing/project_data.py

  /opt/dataiku/python-code-envs/data_collection/bin/python \
    unit_testing/instance_data.py

  /opt/dataiku/python-code-envs/data_collection/bin/python \
    unit_testing/audit_logs.py

Outputs:
- RAW: `partitioned_data/raw/.../*.json.gz`
- RAW errors: `partitioned_data/raw_errors/.../*.json`
- SILVER: `partitioned_data/silver/.../*.parquet`
- SILVER DQ failures: `partitioned_data/silver_fail/.../*.parquet`
- SILVER DQ reasons: `partitioned_data/silver_fail/.../*.dq.json` (or `.dq.json.gz` for audit)

Note: This script expects `python-lib/` to be importable.
"""
