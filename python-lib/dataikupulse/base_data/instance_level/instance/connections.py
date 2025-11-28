import pandas as pd

conn_mapping = {
    "sql_database": [
        "Snowflake", "Databricks", "Redshift", "BigQuery", "Synapse",
        "FabricWarehouse", "PostgreSQL", "MySQL", "SQLServer", "Oracle",
        "Teradata", "AlloyDB", "Athena", "Greenplum", "Vertica", "SAPHANA",
        "Netezza", "Trino", "TreasureData", "Denodo", "JDBC"
    ],
    "file_based": ["Filesystem", "FTP", "SSH"],
    "cloud_storage": ["EC2","Azure", "GCS", "SharePointOnline"],
    "hadoop": ["HDFS"],
    "nosql": ["MongoDB", "Cassandra", "ElasticSearch"],
    "managed_model_depl": ["SageMaker", "AzureML", "VertexAIModelDeployment", "DatabricksModelDeployment"],
    "llm_mesh": [
        "MistralAI", "Pinecone", "CustomLLM", "AzureLLM", "Bedrock",
        "HuggingFaceLocal", "VertexAILLM", "StabilityAI", "Cohere",
        "SageMaker-GenericLLM", "AzureAISearch", "Anthropic",
        "DatabricksLLM", "SnowflakeCortex", "AzureOpenAI", "OpenAI"
    ],
    "other": ["Twitter", "Kafka", "SQS"]
}

reverse_lookup = {
    value: tag
    for tag, values in conn_mapping.items()
    for value in values
}


def main(self, client, client_d = {}):
    connections = client.list_connections_names(connection_type="all")
    dfs = []
    for conn in connections:
        conn_handle = client.get_connection(name=conn)
        settings = conn_handle.get_settings()
        d = settings.settings
        dfs.append(pd.json_normalize(d))
    df = pd.concat(dfs, ignore_index=True)
    df["connection_category"] = df["type"].map(reverse_lookup).fillna("UNKNOWN")
    return df