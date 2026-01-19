import sys
sys.dont_write_bytecode = True

import logging
import streamlit as st
import dataiku
from backend import settings
from backend.duck_db import init_duckdb

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.DEBUG)


# -----------------------------------------------------------------------------
# Load css
def load_css(path: str):
    with open(path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

load_css("assets/css/custom.css")

# -----------------------------------------------------------------------------
# Capture User (if applicable)
DEBUG = False
request_headers = dict(st.context.headers)
try:
    auth_info_browser = dataiku.api_client().get_auth_info_from_browser_headers(request_headers)
    groups = [item.lower() for item in auth_info_browser["groups"]]
except:
    groups = []
if "administrators" in groups:
    DEBUG = True

# -----------------------------------------------------------------------------
# Check DuckDB
if not settings.DB_PATH.exists():
    init_duckdb.initialize_database(reset=False)

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
about = st.Page("pages/main/about.py", title="About")
faq = st.Page("pages/main/faq.py", title="FAQ")
debug = st.Page("pages/main/debug.py", title="DEBUG")

# Infrastructure
platform  = st.Page("pages/infrastructure/platform.py",  title="Platform")

# Insights
users = st.Page("pages/insights/users.py", title="Users")
projects  = st.Page("pages/insights/projects.py",  title="Projects")
datasets  = st.Page("pages/insights/datasets.py",  title="Datasets")
recipes  = st.Page("pages/insights/recipes.py",  title="Recipes")
scenarios  = st.Page("pages/insights/scenarios.py",  title="Scenarios")

# Usage
development = st.Page("pages/usages/development.py", title="Development")

# -----------------------------------------------------------------------------
# Navigation Panel
pages = {
    "Pulse": [
        home,
        about,
        faq,
    ],
    "Infrastructure": [
        platform,
    ],
    "Insight Patterns": [
        users,
        projects,
        datasets,
        recipes,
        scenarios,
    ],
    "Usage Overview": [
        development,
    ],
    "DEBUG": [
        debug,
    ],
}
if not DEBUG:
    del pages["DEBUG"]
if not settings.monitor_os:
    del pages["Infrastructure"]

pg = st.navigation(pages, position="top")
pg.run()

# -----------------------------------------------------------------------------
# EOF