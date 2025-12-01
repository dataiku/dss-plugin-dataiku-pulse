# Modules
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet
from google.cloud import storage
import base64
import dataiku
import streamlit as st
import pandas as pd
import yaml
import duckdb
import os
import shutil
import time
import sqlparse
import logging
import json

# -----------------------------------------------------------------------------
# Logger
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.ERROR)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# DuckDB
duckdb_path = "/tmp/dataikupulse"
duckdb_name = "dataikupulse.duckdb"
duckdb_home = f"{duckdb_path}/{duckdb_name}"

# -----------------------------------------------------------------------------
# Queries loader
def load_yaml(path):
    script_dir = os.path.dirname(os.path.realpath(__file__))
    try:
        yaml_path = os.path.join(script_dir, path)
        with open(yaml_path, "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        raise Exception(e)
    return config

def render_query(query_str, **kwargs):
    return query_str.format(**kwargs)

## Queries
queries = load_yaml("./queries.yaml")

# -----------------------------------------------------------------------------
# BLOB Folder

## GCS HMAC generator
def derive_key_from_password(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))

def decrypt_string(ciphertext: bytes, password: str, salt: bytes) -> str:
    key = derive_key_from_password(password, salt)
    f = Fernet(key)
    return f.decrypt(ciphertext).decode()

## Basic Folder Information / Blob Path
folder = dataiku.Folder(
    lookup="partitioned_data",
    project_key=dataiku.default_project_key(),
    ignore_flow=True
)

## Pull Connection Name From Admin Settings
client = dataiku.api_client()
project_handle = client.get_default_project()
folder_handle = project_handle.get_managed_folder(odb_id=folder.get_id())
connection_name = folder_handle.get_settings().settings["params"]["connection"]
connection_handle = client.get_connection(name=connection_name)
## Pull Connection Setup/Permissions
connection_type = connection_handle.get_info()["type"]
if connection_type == "EC2":
    blob_bket = folder.get_info()["accessInfo"]["bucket"]
    blob_root = folder.get_info()["accessInfo"]["root"][1:]
    blob_header = "s3"
    blob_module = queries["blob_setup"]["aws_modules"]
    aws_region = connection_handle.get_info()["params"]["regionOrEndpoint"]
    credentials_mode = connection_handle.get_info()["params"]["credentialsMode"]
    if credentials_mode == "KEYPAIR":
        accessKey = connection_handle.get_info()["params"]["accessKey"]
        secretKey = connection_handle.get_info()["params"]["secretKey"]
        logger.error("KEYPAIR NEEDS TO BE INIT")
        raise Exception("Failed to find proper BLOB information. Check logs for detail.")
    elif credentials_mode == "STS_ASSUME_ROLE":
        assume_role_arn = connection_handle.get_info()["params"]["stsRoleToAssume"]
        blob_credentials = render_query(
            queries["blob_setup"]["aws_headers_assume"],
            assume_role_arn=assume_role_arn,
            aws_region=aws_region
        )
    elif credentials_mode == "ENVIRONMENT":
        logger.error("Do not accept ENVIRONMENT MODE")
        raise Exception("Failed to find proper BLOB information. Check logs for detail.")

elif connection_type == "Azure":
    blob_bket = folder.get_info()["accessInfo"]["container"]
    blob_root = folder.get_info()["accessInfo"]["root"][1:]
    blob_header = "az"
    blob_module = queries["blob_setup"]["azure_modules"]
    storageAccount = connection_handle.get_info()["params"]["storageAccount"]
    credentials_mode = connection_handle.get_info()["params"]["authType"]
    if credentials_mode == "SHARED_KEY":
        accessKey = connection_handle.get_info()["params"]["accessKey"]
        logger.error("SHARED KEY NEEDS TO BE INIT")
        raise Exception("Failed to find proper BLOB information. Check logs for detail.")
    elif credentials_mode == "OAUTH2_APP":
        tenant_id = connection_handle.get_info()["params"]["tenantId"]
        client_id = connection_handle.get_info()["params"]["appId"]
        client_secret = connection_handle.get_info()["params"]["appSecret"]
        blob_credentials = render_query(
            queries["blob_setup"]["azure_oauth2_headers"],
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            account_name=storageAccount
        )

elif connection_type == "GCS":
    blob_bket = folder.get_info()["accessInfo"]["bucket"]
    blob_root = folder.get_info()["accessInfo"]["root"][1:]
    blob_header = "gs"
    try:
        variables = project_handle.get_variables()
        gcs_hmac = variables["local"].get("gcs_hmac", False)
        salt = base64.b64decode(gcs_hmac["salt"])
        ciphertext = base64.b64decode(gcs_hmac["ciphertext"])
        hmac_secret = decrypt_string(ciphertext, "DF2!&sEkm)f4}i99,e&9bS:Wj", salt)
        blob_module = queries["blob_setup"]["gcp_modules"]
        blob_credentials = render_query(
            queries["blob_setup"]["gcp_headers"],
            hmac_id=gcs_hmac["access_key"],
            hmac_secret=hmac_secret
        )
    except Exception as e:
        logger.error(f"Failed to get HMAC Key and Secret: {e}")
        st.error("Failed to get HMAC Key and Secret. Check logs for more details.")

else:
    logger.error("Unknown Blob storage type")
    logger.error(folder.get_info())
    raise Exception("Failed to find proper BLOB information. Check logs for detail.")

# -----------------------------------------------------------------------------
# Dataiku Folder Partition
def build_partition_df():
    df = pd.DataFrame(folder.list_partitions(), columns=["partitions"])
    df[["instance_name", "category", "module", "date"]] = df["partitions"].str.split("|", expand=True)
    df["date"] = pd.to_datetime(df["date"])
    return df
# -----------------------------------------------------------------------------
# DuckDB -- LOADING
def create_duckdb():
    progress_text = "Creating DuckDB"
    progress_bar = st.progress(0, text=progress_text)
    status_text = st.empty()
    duckdb_path = os.path.dirname(duckdb_home)
    if os.path.exists(duckdb_path):
        shutil.rmtree(duckdb_path)
    try:
        os.makedirs(duckdb_path, exist_ok=True)
    except OSError as e:
        logger.error(e)
        return False
    try:
        con = duckdb.connect(duckdb_home)
        con.close()
    except Exception as e:
        logger.error(e)
        return False
    progress = int(1 / 1 * 100)
    progress_bar.progress(progress, text=progress_text)
    status_text.text(f"DuckDB Created")
    time.sleep(1)
    progress_bar.empty()
    status_text.empty()
    return True

def load_base_tables(partition_df):
    # Create initial partition table
    load_table_df("CREATE TABLE partition_table AS SELECT * FROM df_view", partition_df)
    # Create base tables
    limited_df = partition_df[
        ((partition_df["module"] == "metadata") | (partition_df["category"] == "instance"))
    ]
    limited_df = limited_df["date"].value_counts().reset_index().sort_values(by=["count", "date"], ascending=False)
    base_data_df = partition_df[
        (partition_df["date"] == limited_df.iloc[0,0])
        &((partition_df["module"] == "metadata") | (partition_df["category"] == "instance"))
    ]
    if base_data_df.empty:
        raise Exception("No Base Data Grps")
    base_data_grps = base_data_df.groupby(by=["category", "module"])
    total_grps = len(base_data_grps)
    progress_text = "Copying Base Data into database"
    progress_bar = st.progress(0, text=progress_text)
    status_text = st.empty()
    for i, value in enumerate(base_data_grps, start=1):
        index = value[0]
        grp = value[1]
        table_name = "_".join(index)
        paths = []
        for row in grp.itertuples():
            partition = getattr(row, "partitions")
            paths += folder.list_paths_in_partition(partition=partition)
        path_list = ", ".join(f"'{blob_header}://{blob_bket}/{blob_root}/{p[1:]}'" for p in paths)
        query = render_query(
            queries["base_data"]["main"],
            table_name=table_name,
            path_list=path_list
        )
        r = load_parquet_sql(query)
        if not r:
            raise Exception("Failed to load DuckDB. Check logs for errors.")
        progress = int(i / total_grps * 100)
        progress_bar.progress(progress, text=progress_text)
        status_text.text(f"Imported {i}/{total_grps} Base Data ({table_name})")
    time.sleep(1)
    progress_bar.empty()
    status_text.empty()
    return True

def load_dataiku_usage(partition_df):
    progress_text = "Copying Dataiku Usage Data into database"
    progress_bar = st.progress(0, text=progress_text)
    status_text = st.empty()
    # Build Queries
    instances = partition_df["instance_name"].unique().tolist()
    paths = [
        f"{blob_header}://{blob_bket}/{blob_root}/{instance}/dataiku_usage/**/*.parquet"
        for instance in instances
    ]
    usage_queries = [
        render_query( queries["dataiku_usage"]["overview"], paths = paths)
    ]
    # Get max date overall
    limited_df = partition_df[
        ((partition_df["module"] == "metadata") | (partition_df["category"] == "instance"))
    ]
    limited_df = limited_df["date"].value_counts().reset_index().sort_values(by=["count", "date"], ascending=False)
    history_df = partition_df[
        (partition_df["date"] == limited_df.iloc[0,0])
        &((partition_df["module"] != "metadata") & (partition_df["category"] != "instance"))
    ]
    for row in history_df.itertuples():
        category = getattr(row, "category")
        module = getattr(row, "module")
        paths = [
            f"{blob_header}://{blob_bket}/{blob_root}/{instance}/{category}/{module}/**/*.parquet"
            for instance in instances
        ]
        usage_queries.append(
            render_query(queries["dataiku_usage"]["module"], paths = paths)
        )
    # Build Wrapper
    total_queries = len(usage_queries)
    for i, query in enumerate(usage_queries, start=1):
        start_index = query.find("TABLE ") + 6
        end_index = query.find(" AS\n")
        table_name = query[start_index:end_index]
        r = load_parquet_sql(query)
        logger.error(query)
        if not r:
            raise Exception("Failed to load DuckDB. Check logs for errors.")
        progress = int(i / total_queries * 100)
        progress_bar.progress(progress, text=progress_text)
        status_text.text(f"Imported {i}/{total_queries} Dataiku Usage Data ({table_name})")
    time.sleep(1)
    progress_bar.empty()
    status_text.empty()
    return True

def load_additional_tables():
    total_tables = len(queries["addon_data"])
    progress_text = "Creating Additional Tables into database"
    progress_bar = st.progress(0, text=progress_text)
    status_text = st.empty()
    for i, key in enumerate(queries["addon_data"], start=1):
        query = queries["addon_data"][key]
        r = load_table_sql(query)
        if not r:
            raise Exception("Failed to load DuckDB. Check logs for errors.")
        progress = int(i / total_tables * 100)
        progress_bar.progress(progress, text=progress_text)
        status_text.text(f"Created {i}/{total_tables} Additional Tables ({key})")
    time.sleep(1)
    progress_bar.empty()
    status_text.empty()
    return True

def load_parquet_sql(query):
    try:
        with duckdb.connect(duckdb_home) as con:
            con.execute(f"{blob_module}")
            con.execute(f"{blob_credentials}")
            df = con.execute(query).df()
    except Exception as e:
        print(e)
        logger.error(e)
        return False
    return True

def load_table_sql(query):
    try:
        with duckdb.connect(duckdb_home) as con:
            df = con.execute(query).df()
    except Exception as e:
        logger.error(e)
        return False
    return True

def load_table_df(query, df):
    try:
        with duckdb.connect(duckdb_home) as con:
            con.register("df_view", df)
            df = con.execute(query).df()
    except Exception as e:
        logger.error(e)
        return False
    return True

# -----------------------------------------------------------------------------
# DuckDB -- Querying
def filters_conversion(filters):
    final_filter = []
    for filter in filters.keys():
        if not filters[filter]:
            continue
        if filter == "enabled":
            if len(filters[filter]) != 1:
                continue
            b = str(filters["enabled"][0]).upper()
            s = f"mpk.{filter} = {b}"
            final_filter.append(s)
        else:
            s = f"mpk.{filter} IN ("
            in_clause = ", ".join(f"'{x}'" for x in filters[filter])
            s += f"{in_clause})"
            final_filter.append(s)
    return final_filter

def build_sql(query: dict, filters: dict) -> str:
    # SELECT
    select_clause = "SELECT " + ", ".join(query.get("select", ["*"]))
    # FROM
    from_clause = "FROM " + ", ".join(query.get("from", []))
    # JOIN
    join_clause = ""
    if query.get("join"):
        join_clause = "\n" + "\n".join(query["join"])
    # WHERE
    filter_clause = ""
    if filters and "_metadata" in from_clause:
        filter_clause = "INNER JOIN metadata_primary_keys AS mpk ON ("
        for og_tbl in query["from"]:
            if "AS" in og_tbl.upper():
                tbl = og_tbl.split(" ")
                tbl = tbl[-1]
            else:
                tbl = og_tbl
            if "users_metadata" in og_tbl.lower():
                filter_clause += f"{tbl}.instance_name = mpk.instance_name AND {tbl}.login = mpk.login"
            else:
                filter_clause += f"{tbl}.instance_name = mpk.instance_name AND {tbl}.project_key = mpk.project_key"
        filter_clause += ")"
        filters = filters_conversion(filters)
        query["where"] += filters
    where_clause = ""
    if query.get("where"):
        where_clause = "\nWHERE " + " AND ".join(query["where"])
    # GROUP BY
    group_clause = ""
    if query.get("group"):
        group_clause = "\nGROUP BY " + ", ".join(query["group"])
    # ORDER BY
    order_clause = ""
    if query.get("order"):
        order_clause = "\nORDER BY " + ", ".join(query["order"])
    # Put it all together
    sql = "\n".join([select_clause, from_clause, join_clause, filter_clause, where_clause, group_clause, order_clause])
    sql = sqlparse.format(sql, reindent=True, keyword_case='upper')
    return sql

def query_build_sql(query, filters = {}, debug=False):
    query = build_sql(query, filters)
    if debug:
        import streamlit as st
        st.write(query)
        print(query)
    try:
        with duckdb.connect(duckdb_home, read_only=True) as con:
            df = con.execute(query).df()
    except Exception as e:
        logger.error(e)
        return pd.DataFrame()
    return df

def query_direct_sql(query):
    try:
        with duckdb.connect(duckdb_home, read_only=True) as con:
            df = con.execute(query).df()
    except Exception as e:
        logger.error(e)
        return pd.DataFrame()
    return df