import streamlit as st
st.set_page_config(initial_sidebar_state="collapsed")
st.markdown(f"""# 📊 Dataiku Pulse Dashboard

## 🧪 Debug & Development Mode

When running in debug or development mode:

- Additional query logging may be enabled
- Metrics and graphs may be recomputed more frequently
- Logged data is used to improve future optimizations, not production monitoring

Production usage prioritizes stability and clarity.

---

## 📝 Extra Notes

- Not every metric is intended to become a long-term standard
- Some insights are exploratory by design
- Frequently used metrics may be promoted to GOLD standards over time
- Customer-specific analytics are expected to evolve separately from system-defined metrics

Pulse is designed to **grow with the platform**, not constrain it.

---

## 📩 Contact Support

The **Dataiku Pulse Dashboard** is provided as a **Platinum Support Level service**.

Support is available to administrative and leadership stakeholders through your **Technical Account Manager**.  
To help ensure a timely and effective response, please include relevant context when reaching out (such as the page, metric, or timeframe in question).

Please note that general Dataiku Support is not able to provide assistance for this dashboard or related services.

---

""")