import logging
import uuid
import re
from datetime import datetime

logger = logging.getLogger(__name__)

def normalize_sql(sql: str) -> str:
    """
    Normalize SQL so logically identical queries map to the same string.
    Intended for workload analysis, NOT execution.

    Normalizations:
    - lowercase
    - remove comments
    - collapse whitespace
    - replace literals (strings, numbers, dates) with '?'
    - normalize IN (...) lists
    """
    s = sql

    # -----------------------------
    # Remove SQL comments
    # -----------------------------
    # -- single line comments
    s = re.sub(r"--.*?$", "", s, flags=re.MULTILINE)
    # /* multi-line comments */
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)

    # -----------------------------
    # Lowercase
    # -----------------------------
    s = s.lower()

    # -----------------------------
    # Replace quoted strings
    # -----------------------------
    # 'abc', '2025-01-01', etc.
    s = re.sub(r"'([^']|'')*'", "?", s)

    # -----------------------------
    # Replace numeric literals
    # -----------------------------
    # integers and floats
    s = re.sub(r"\b\d+(\.\d+)?\b", "?", s)

    # -----------------------------
    # Normalize IN (...) lists
    # -----------------------------
    # in (?,?,?,?) -> in (?)
    s = re.sub(r"in\s*\(\s*(\?\s*,\s*)+\?\s*\)", "in (?)", s)

    # -----------------------------
    # Collapse whitespace
    # -----------------------------
    s = re.sub(r"\s+", " ", s).strip()

    return s


def log_query(
    conn,
    sql_text: str,
    *,
    normalized_sql: str | None = None,
    page: str | None = None,
    session_id: str | None = None,
):
    """
    Persist query metadata into query_log table.
    This function MUST NOT raise — logging failures should never break queries.
    """

    try:
        # Fallbacks
        ts = datetime.utcnow()

        if session_id is None:
            session_id = str(uuid.uuid4())

        # If caller didn’t pass normalized SQL, store raw
        if normalized_sql is None:
            normalized_sql = sql_text

        conn.execute(
            """
            INSERT INTO query_log (
                ts,
                session_id,
                page,
                sql_text
            )
            VALUES (?, ?, ?, ?)
            """,
            [
                ts,
                session_id,
                page,
                normalized_sql,
            ],
        )

    except Exception:
        # NEVER allow query logging to break the app
        logger.debug("Failed to log query", exc_info=True)