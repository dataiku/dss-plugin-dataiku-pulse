import sys
sys.dont_write_bytecode = True

import streamlit as st
from sage.src import dss_duck
import os

# -----------------------------------------------------------------------------
# Initialization
if not os.path.isfile(dss_duck.funcs.duckdb_home):
    dss_duck.initiate_db()

# -----------------------------------------------------------------------------
# Setup streamlit configs
st.set_page_config(
    page_title="Dataiku Sage Dashboard",
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
genai_llm = st.Page("pages/insights/genai_llm.py", title="GEN AI / LLM")

# -----------------------------------------------------------------------------
# Dataiku Usage Patterns
dataiku_usage = st.Page("pages/usages/dataiku_usage.py", title="Dataiku Usage")

# -----------------------------------------------------------------------------
# Navigation Panel
pages = {
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
        genai_llm
    ],
    "Usage Patterns": [
        dataiku_usage
    ]
}

pg = st.navigation(pages, position="top")
pg.run()

# -----------------------------------------------------------------------------
# EOF