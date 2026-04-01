# Future improvements: data-gather-audit-logs

This document tracks potential follow-ups for the `data-gather-audit-logs` macro.

## Reliability
- Add retry/backoff when reading audit files and when uploading outputs.
- Handle partially corrupted JSON lines gracefully (skip and count).

## Performance
- Avoid concatenating many large log files at once; stream/process file-by-file.
- Consider chunked reading (iterating JSON lines) for very large logs.

## Extensibility
- Add more processors (user auditing, MAU eligibility, etc.) under `audit_logs_modules/`.
- Allow processors to declare their partition column (default: `dataiku_category`).

## Observability
- Add ResultTable rows for:
  - number of files scanned/read
  - number of rows delta-filtered
  - backup written / backup path
  - top error categories
