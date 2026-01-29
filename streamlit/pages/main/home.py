import streamlit as st
st.set_page_config(initial_sidebar_state="collapsed")
st.markdown(f"""# 📊 Dataiku Pulse Dashboard

## Overview

The **Dataiku Pulse Dashboard** provides a structured, scalable view into how activity, adoption, and usage patterns evolve across multiple domains over time.

Rather than acting as a static reporting surface, Pulse is designed as an **insight-oriented analytics layer** built on curated platform metadata. It combines metrics, trends, and activity signals to help users understand **what is happening**, **where activity is concentrated**, and **how behavior is changing** — without requiring direct interaction with raw event data.

The dashboard is intentionally modular. Each domain is analyzed independently using a consistent framework, allowing new categories to be added incrementally while preserving performance, clarity, and comparability.

Pulse focuses on surfacing **meaningful signals**, not exhaustive detail. Every metric and chart is designed to answer a specific question and contribute to a broader understanding of platform behavior.

---

## 🎯 Goals of the Dashboard

The primary goals of Pulse are to:

- Provide clear, high-level signals about Dataiku usage and growth
- Surface trends early (growth, decline, concentration of activity)
- Support operational and strategic decision-making
- Scale gracefully as historical data, metrics, and user-defined analytics increase
- Separate insight from raw usage, avoiding noisy or misleading interpretations

Pulse intentionally avoids “everything dashboards.” Each metric and chart is designed to answer a specific question.

---

## Intended Audience

The **Dataiku Pulse Dashboard** is intended for **administrative and leadership audiences** responsible for overseeing platform usage, adoption, and overall health.

### Platform & System Administrators
Administrators use Pulse to:
- Monitor high-level activity and adoption patterns
- Identify trends, growth, or anomalies across domains
- Support operational planning and governance decisions
- Inform where optimization, standardization, or deeper investigation may be required

Pulse is designed to provide administrators with **clear signals**, not raw operational logs.

### Leadership & Decision Makers
Leadership uses Pulse to:
- Gain a concise view of platform evolution over time
- Understand where activity and investment are concentrated
- Support strategic planning, capacity discussions, and prioritization
- Ask informed follow-up questions without needing technical detail

The dashboard is intentionally structured to be **interpretable without deep platform expertise**.

---

## 🚀 Final Thought

The Dataiku Pulse Dashboard is not about showing everything.  
It is about showing **the right information**, at the right level, to the right audience.

If a chart or metric does not help answer a real question — it does not belong here.

---

""")