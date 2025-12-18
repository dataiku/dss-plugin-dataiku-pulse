FLAT_COLUMNS = {
    # Event identity / classification
    "severity",
    "logger",
    "topic",
    "audittopic",
    "msgtype",
    "msgtypebase",
    "dataiku_category",

    # Actor / auth context
    "login",
    "authsource",
    "authvia",
    "user",

    # Request / network
    "callpath",
    "clientip",
    "originalip",
    "xforwardedfor",

    # Time
    "timestamp",
    "date",

    # Instance
    "instance_name",
    "project_key",
}

GENAI_LLM = {
    "llmconnection",
}

def get_flat_cols(t):
    FINAL_FLAT_COLUMNS = FLAT_COLUMNS
    if t == "GENAI_LLM":
        FINAL_FLAT_COLUMNS = FLAT_COLUMNS | GENAI_LLM
    return FINAL_FLAT_COLUMNS
