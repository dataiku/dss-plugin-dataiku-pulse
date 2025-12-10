import streamlit as st
from src import dss_duck

st.set_page_config(initial_sidebar_state="collapsed")

# -----------------------------------------------------------------------------
# Variables
if "partition_df" not in st.session_state:
    st.session_state["partition_df"] = dss_duck.funcs.query_direct_sql("SELECT * FROM partition_table")

try:
    if not st.session_state["partition_df"].empty:
        partition = st.session_state["partition_df"]["date"].max()
    else:
        st.session_state["partition_df"] = dss_duck.funcs.query_direct_sql("SELECT * FROM partition_table")
        partition = st.session_state["partition_df"]["date"].max()
        partition = False
except:
    partition = "Error to load proper partition date information"

# -----------------------------------------------------------------------------
# Homepage
title = "# 📊 Dataiku PULSE Insights Dashboard"
if st.session_state.DEBUG:
    title = "# 📊 Dataiku PULSE Insights Dashboard -- DEBUG MODE"

st.markdown(f"""{title}

## Overview

This dashboard provides key administrative insights into the Dataiku platform to help platform owners, administrators, and governance teams monitor usage, performance, project activity, and compliance metrics.

* PULSE
    * People (Developers, Readers, Consumers)
    * Usage (Objects and Patterns)
    * Lifecycle (Scope, Design, Govern, Automation)
    * System Evaluation (Performance and Expectations)
* **Latest Snapshot Partition:** {partition}

---

## 🧭 Goals (TBD)

- Monitor platform health and system performance
- Track user and project activity
- Identify inactive or stale projects
- Provide audit trails for security and governance
- Enable better resource planning and license usage insights


---

## 📅 Refresh Schedule

| Data Source           | Frequency |
|-----------------------|-----------|
| Log Ingestion         | Daily     |
| Insights & Statistics | Instant   |

---

## 👤 Access & Permissions

> Only users in the `administrative` group have full access to this dashboard.

---

## 📌 Notes

- This dashboard is built to be modular. Designed for ease of use and scale.
- For questions or enhancements, contact the **Platform Admin Team** at `Stephen.Mazzei@dataiku.com`.

---

## 🔃 Refresh Dataiku PULSE Insight Data

* This is an "administration" function and is locked behind a password.

---
""")


pwd = st.text_input("Password", type="password")
reload_duckdb = st.button("Complete Reload DuckDB")
if reload_duckdb:
    if pwd == "dataikupulse2026":
        dss_duck.initiate_db()
    else:
        st.error("Invalid password.")


toggle_debug = st.button("Toggle Debug")
if toggle_debug:
    if pwd == "dataikupulse2026":
        plugin_handle = dss_duck.funcs.client.get_plugin(plugin_id="dataiku-pulse")
        settings = plugin_handle.get_settings()
        param_set = settings.get_parameter_set(parameter_set_name="params-dashboard-instance")
        preset = param_set.get_preset(preset_name=param_set.list_preset_names()[0])
        st.session_state.DEBUG = preset.get_raw()["pluginConfig"]["pulse_dashboard_debug"]
        st.rerun()
    else:
        st.error("Invalid password.")