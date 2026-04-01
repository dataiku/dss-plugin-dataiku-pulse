# Future improvements: data-gather-instance

This document tracks potential follow-ups for the `data-gather-instance` macro.

## Selectivity
- Maintain `collection_exclusions/instance_data.yaml` as new DSS releases add `client.list_*` methods.
- Maintain `collection_exclusions/instance_project_inclusion.yaml` for project-invariant `project_handle.list_*` methods collected once.

## Reliability
- Add small retry/backoff for transient DSS/API errors (429/502/503/timeouts).
- For known schema-problem methods, consider RAW-only mode (skip SILVER) or method-specific sanitization.

## Observability
- Add more ResultTable metrics:
  - excluded method count
  - inclusion list failures by method (already partially implemented)
  - elapsed time

## Data model
- Consider adding a `source_scope` column (instance vs inclusion) if needed.
- Consider adding a `worker_project_key` column for inclusion-derived outputs.
