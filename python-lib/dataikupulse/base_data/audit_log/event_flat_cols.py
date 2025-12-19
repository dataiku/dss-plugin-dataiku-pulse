FLAT_COLUMNS = [
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
]

ADMINISTRATION = []
APIS = []
APPLICATION_DESIGNER = []
CODING = []
DATASET = []
DEPLOYER = []
FLOW = []
FOLDERS = []
GENAI_LLM = []
MLOPS = []
OTHER = []
PLUGINS = []
PROJECTS = []
READING_LISTING = []
SCENARIOS = []
USER_MAINTENANCE = []
VISUAL_RECIPES = []
WEBAPPS = []
WIKI_ARTICAL_DISCUSSIONS = []


def get_flat_cols(t):
    FINAL_FLAT_COLUMNS = FLAT_COLUMNS
    if t == "genai_llm":
        FINAL_FLAT_COLUMNS = FLAT_COLUMNS + GENAI_LLM
    return FINAL_FLAT_COLUMNS
