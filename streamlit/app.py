import streamlit as st
import sys
import os

sys.dont_write_bytecode = True

from src import dss_duck

# -----------------------------------------------------------------------------
# Initialization
if not os.path.isfile(dss_duck.funcs.duckdb_home):
    dss_duck.initiate_db()

# -----------------------------------------------------------------------------
# Setup streamlit configs
st.set_page_config(
    page_title="Dataiku PULSE Insight Dashboard",
    page_icon="🏂",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -----------------------------------------------------------------------------
# Pulse Home
home = st.Page("pages/main/home.py", title="Home", default=True)

# -----------------------------------------------------------------------------
# Platform Insights

## Operating System
disk_space = st.Page("pages/insights/disk_space.py", title="Disk Space")
## Dataiku - Client
users = st.Page("pages/insights/users.py", title="Users")
## Dataiku - Project
projects  = st.Page("pages/insights/projects.py",  title="Projects")
datasets  = st.Page("pages/insights/datasets.py",  title="Datasets")
recipes   = st.Page("pages/insights/recipes.py",   title="Recipes")
scenarios = st.Page("pages/insights/scenarios.py", title="Scenarios")
llms_connections = st.Page("pages/insights/llms_connections.py", title="LLM Connections")

# -----------------------------------------------------------------------------
# Dataiku Usage Patterns
dataiku_usage  = st.Page("pages/usages/dataiku_usage.py", title="Dataiku Usage")
genai_llm = st.Page("pages/usages/genai_llm.py", title="GEN AI / LLM")

# -----------------------------------------------------------------------------
# 
debug = st.Page("pages/main/debug.py", title="DEBUG")

# -----------------------------------------------------------------------------
# Navigation Panel
default_pages = {
    "PULSE Home": [
        home
    ],
    "Operating System": [
        disk_space
    ],
    "Platform Insights": [
        users,
        projects,
        datasets,
        recipes,
        scenarios,
        llms_connections
    ],
    "Usage Patterns": [
        dataiku_usage,
        genai_llm
    ]
}
if st.session_state.DEBUG:
    pages = default_pages
    pages["DEBUG"] = [debug]
else:
    pages = default_pages

pg = st.navigation(pages, position="top")
pg.run()

# -----------------------------------------------------------------------------
# EOF