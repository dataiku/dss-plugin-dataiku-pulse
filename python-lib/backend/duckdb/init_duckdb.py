from duckdb_handle import create_conn

def build_gold_tables():
    try:
        create_conn.reset_duckdb(reset=reset)
    except Exception as e:
        raise Exception(e)
    return