-- Generic "latest row per key" builder
--
-- Parameters:
--   {base_table_name}: name of the materialized table
--   {view_table_name}: name of the input view/table
--   {partition_keys}: comma-separated key columns

CREATE OR REPLACE TABLE {base_table_name} AS
WITH ranked AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY {partition_keys}
      ORDER BY run_ts DESC, partition_date DESC
    ) AS rn
  FROM {view_table_name}
)
SELECT
  * EXCLUDE (rn)
FROM ranked
WHERE rn = 1;
