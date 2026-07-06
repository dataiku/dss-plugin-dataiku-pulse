# Pulse security notes

Known security-relevant behaviors of the plugin, with rationale. Review before
deploying to a hardened environment.

## Hardcoded GCS HMAC decryption password (accepted risk)

`python-lib/shared_duckdb/storage_config.py` decrypts the GCS HMAC secret
stored in project variables using a password hardcoded in the source
(`gcp_credentials`, see the `decrypt_string(...)` call).

This is deliberate and was explicitly kept (2026-07 hardening review): the
value stored in project variables is **obfuscation, not encryption**. Anyone
with read access to the plugin source and the project variables can recover
the secret. The threat this protects against is casual disclosure (a secret
readable in cleartext in the project-variables UI / API dumps), nothing more.

Operational guidance:

- Treat the `gcs_hmac` project variable as if it were a cleartext credential:
  restrict project access accordingly.
- Prefer service-account-based GCS connections over HMAC keys where possible;
  the HMAC path exists for DuckDB's S3-compat GCS access.
- Rotating the HMAC key rotates the actual credential; the obfuscation
  password does not need rotation (it protects nothing by itself).

## `/api/duckdb/query` raw-SQL debug endpoint

The dashboard backend exposes `GET /api/duckdb/query?q=...` which executes
arbitrary SQL against the dashboard DuckDB database.

Gating:

- **In DSS**: only enabled when the standard project variable `debug` is
  `true` on the dashboard project. Keep it off outside active debugging. The
  DuckDB file contains the full gold layer (all collected metadata), so anyone
  with webapp access can read all of it while enabled.
- **Local dev**: since the 2026-07 hardening release, local dev no longer
  enables it implicitly — set `PULSE_DEV_ALLOW_RAW_SQL=1` explicitly.

## Credential rendering into DuckDB SQL

Connection credentials (AWS keys, Azure keys, GCS HMAC) are rendered into
`CREATE SECRET` statements. Values are single-quote escaped, and values
containing `{`/`}` are rejected with a clear error (they previously broke
`str.format` cryptically). Credentials never leave the recipe/backend process;
they are not written to gold outputs.

## TLS

The `ignore_certs` plugin setting disables certificate verification for
hub→worker API calls. Only use it for lab instances with self-signed
certificates; prefer installing the CA into the code-env trust store.

## Concurrency model (dashboard backend)

Multiple gunicorn workers coordinate DuckDB initialization **only** via the
fcntl lock file under `PULSE_DUCKDB_DIR` (`.duckdb_init.lock`). The lock
directory is created at startup (since the 2026-07 release — previously the
directory was never created and the lock silently could not be acquired, so
workers raced). There is no other cross-process coordination; anything else
that must be once-per-instance belongs behind that lock.
