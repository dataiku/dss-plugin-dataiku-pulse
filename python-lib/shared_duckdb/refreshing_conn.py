"""DuckDB connection proxy that keeps blob-storage credentials fresh.

DSS resolves ENVIRONMENT / STS_ASSUME_ROLE connections to a static STS
session token which the DuckDB secret stores verbatim. Gold builds run for
hours, so the token can expire mid-build ("ExpiredToken: The provided token
has expired" on every S3 request afterwards). The proxy re-creates the secret
(CREATE OR REPLACE SECRET) with freshly resolved credentials on a schedule,
and retries a statement once when an expiry error slips through mid-statement
(all recipe statements are CREATE OR REPLACE / COPY OVERWRITE, so a retry is
safe).
"""

from __future__ import annotations

import logging
import time

from .storage_config import refresh_storage_credentials

logger = logging.getLogger(__name__)

# Statement-boundary refresh cadence. DSS re-resolves the credential through
# the AWS SDK provider chain, which rotates the token as it nears expiry, so
# refreshing this often keeps the secret's token far from its deadline.
REFRESH_INTERVAL_SECONDS = 900.0

_EXPIRED_MARKERS = (
    "expiredtoken",
    "token has expired",
    "security token included in the request is expired",
)


def is_expired_credentials_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _EXPIRED_MARKERS)


class RefreshingConnection:
    """Delegating proxy over a DuckDB connection bound to a storage context.

    Only ``execute``/``executemany``/``sql`` get the refresh-and-retry
    treatment; every other attribute (``close``, ``cursor``, ...) passes
    straight through to the wrapped connection.
    """

    def __init__(self, conn, *, ctx, refresh_interval_seconds: float = REFRESH_INTERVAL_SECONDS):
        self._conn = conn
        self._ctx = ctx
        self._refresh_interval = float(refresh_interval_seconds)
        self._last_refresh = time.monotonic()

    def refresh_credentials(self) -> None:
        refresh_storage_credentials(self._conn, ctx=self._ctx)
        self._last_refresh = time.monotonic()

    def _maybe_refresh(self) -> None:
        if time.monotonic() - self._last_refresh < self._refresh_interval:
            return
        try:
            logger.info("Refreshing DuckDB storage credentials (scheduled)")
            self.refresh_credentials()
        except Exception:
            # A failed proactive refresh must not fail the statement — the
            # current secret may still be valid; the reactive retry below
            # remains as the backstop.
            logger.warning("Scheduled storage credential refresh failed", exc_info=True)

    def _run(self, method_name: str, *args, **kwargs):
        self._maybe_refresh()
        method = getattr(self._conn, method_name)
        try:
            return method(*args, **kwargs)
        except Exception as exc:
            if not is_expired_credentials_error(exc):
                raise
            logger.warning(
                "DuckDB storage credentials expired mid-statement; refreshing secret and retrying once"
            )
            self.refresh_credentials()
            return method(*args, **kwargs)

    def execute(self, *args, **kwargs):
        return self._run("execute", *args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._run("executemany", *args, **kwargs)

    def sql(self, *args, **kwargs):
        return self._run("sql", *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)
