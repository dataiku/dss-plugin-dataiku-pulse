# Future improvements: data-gather-project

This document tracks potential follow-ups for the `data-gather-project` macro.

## Reliability
- Add small retry/backoff for transient DSS/API errors (429/502/503/timeouts).
- Classify errors (permissions vs transient vs data-shape) in `raw_errors` JSON.

## Observability
- Add more ResultTable metrics:
  - excluded method count
  - top failing `list_*` methods (unique)
  - elapsed time

## Data model
- Consider adding a run identifier in path/filename if multiple runs/day must be retained.
- Consider `jsonl.gz` for very large RAW list payloads.

## Enrichment (list → get → settings)

Some DSS objects have richer details accessible via a getter after listing.

Example pattern:
- `list_scenarios()` → loop scenario IDs
- `get_scenario(scenario_id=...)` → `handle.get_settings()` → persist richer settings/details

Recommendations:
- Implement enrichment as *separate outputs* (new categories like `scenarios_settings`) rather than joining back inline.
- Use a small YAML mapping per enrichment target to define:
  - list method name
  - ID field/column
  - getter method name + argument name
  - which settings/raw data to extract
- Keep the enrichment framework generic, but expect per-object mappings (getter signatures and ID fields vary).

## Performance
- Consider capping default `cores` to a safe maximum on very large DSS.
- Consider filtering/short-circuiting known expensive `list_*` methods by default via exclusions.
