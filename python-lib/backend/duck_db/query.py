import logging
import duckdb
from backend.settings import DB_PATH
from backend.duck_db import create_conn

logger = logging.getLogger(__name__)

# -------------------------------------------------------
# Execute SELECT queries & return DataFrame
# -------------------------------------------------------
def query_df(sql: str, *, page=None):
    """
    Executes a SELECT query using a short-lived, read-only DuckDB connection.
    """
    if not sql or not sql.strip():
        raise ValueError("SQL query is empty.")
    
    try:
        logger.debug(f"Executing SELECT query:\n{sql}")
        conn = create_conn.create_connection(read_only=True)
        try:
            return conn.execute(sql).df()
        finally:
            conn.close()
    except Exception:
        logger.exception("Query failed.")
        raise