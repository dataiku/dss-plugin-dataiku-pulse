import streamlit as st
st.set_page_config(initial_sidebar_state="collapsed")
st.markdown(f"""# 📊 Dataiku Pulse Dashboard

## Insights vs Usage

Pulse makes a deliberate distinction between **Insights** and **Usage**.  
These represent different questions, different mental models, and different user needs.

Understanding this distinction is key to using the dashboard effectively.

---

### Insights

**Insights answer the question:**  
**“What is happening in the environment?”**

They are designed to provide a **high-level, outcome-focused view** of platform behavior.

**Characteristics**
- Snapshot-oriented
- Aggregated across domains
- Focused on outcomes and signals
- Useful for assessing system health
- Easy to interpret without operational context
- Suitable for leadership and executive audiences

**Examples**
- Number of projects
- New users over time
- Active projects
- Projects created per month
- High-level growth in advanced feature usage (e.g. LLM adoption)

Insights help answer:
- *Is adoption increasing or slowing?*
- *Where is activity concentrated?*
- *Are there trends that require attention?*

---

### Usage

**Usage answers the question:**  
**“Who is using what, how, and when?”**

It focuses on **behavioral detail and event-level activity**.

**Characteristics**
- Event-based and temporal
- Actor-focused (users, tools, actions)
- Investigative and operational in nature
- High volume and high granularity
- Requires context to interpret correctly

**Examples**
- User journeys across tools
- Project → feature → workflow transitions
- Time spent per tool or feature
- Feature adoption at the user level
- Detailed audit or activity log analysis

Usage helps answer:
- *How are features actually being used?*
- *Which workflows are most common?*
- *What happened before or after a specific event?*

---

### Why These Are Kept Separate

Insights and Usage serve **different audiences with different intent**.

Trying to combine them into a single navigation or experience often creates friction and confusion.

| Persona              | Uses Insights | Uses Usage |
|----------------------|---------------|------------|
| Executive / Manager  | ✅            | ❌         |
| Platform Owner       | ✅            | ✅         |
| Administrator        | ❌            | ✅         |
| Security / Audit     | ❌            | ✅         |
| Data / ML Lead       | ⚠️            | ✅         |

Pulse is intentionally designed to prioritize **Insights** for its primary audience, while allowing Usage-focused analysis to live in separate, purpose-built tools.

This separation keeps the dashboard:
- Focused
- Interpretable
- Scalable as new domains are added

---

## 🧱 Data Layers (High-Level)

Pulse uses a layered data approach to remain performant and flexible:

- **RAW**  
  External metadata sources (parquet, blob storage), accessed lazily via DuckDB macros

- **BASE**  
  Cleaned, reduced, and structured datasets used by most metrics and graphs

- **GOLD**  
  Optimized, materialized datasets created only when justified by real usage patterns

This allows the dashboard to start flexible and become optimized over time — not the other way around.

---

## ⚙️ Caching & Performance Notes

- Results are cached at the **analytic level**, not the database level
- This ensures:
  - Fast UI responsiveness
  - Accurate logging of user intent
  - Predictable refresh behavior

Caches are time-bound and can be cleared by administrators when needed.

---

""")