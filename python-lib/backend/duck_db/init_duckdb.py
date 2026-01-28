from backend.duck_db import create_conn


def build_gold_tables():
    try:
        create_conn.reset_duckdb(reset=True)
    except Exception as e:
        raise Exception(e)
    return