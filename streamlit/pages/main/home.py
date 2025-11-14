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
st.markdown(f"""# 📊 Dataiku PULSE Insights Dashboard

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

""")

if st.button("Refresh"):
    dss_duck.initiate_db()

st.dataframe(dss_duck.funcs.query_direct_sql("SHOW TABLES;"))