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
    if t == GENAI_LLM:
        FLAT_COLUMNS = FLAT_COLUMNS | GENAI_LLM
    return FLAT_COLUMNS
