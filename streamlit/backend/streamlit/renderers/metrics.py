import pandas as pd
import streamlit as st

def get_total_value(df, value_col):
    series = df[value_col].dropna()
    if series.empty:
        total_value = None
    elif pd.api.types.is_numeric_dtype(series): # --- Numeric metrics (int, float) ---
        total_value = series.sum()
    elif pd.api.types.is_bool_dtype(series): # --- Boolean metrics ---
        # Most common boolean (True / False)
        counts = series.value_counts()
        total_value = counts.idxmax()
    else: # --- Categorical / string metrics ---
        counts = series.value_counts()
        max_count = counts.max()
        # All values tied for highest frequency
        top_values = counts[counts == max_count].index.tolist()
        total_value = " | ".join(map(str, top_values))
    return total_value


def render_metric(result):
    df = result["df"]
    meta = result["meta"]
    
    value_col = meta["value_column"]
    delta_col = meta.get("delta_pct_column")
    label = meta["label"]
    groupby = meta.get("groupby", [])
    metric_id = meta["id"]

    modal_state_key = f"{metric_id}_open"

    if modal_state_key not in st.session_state:
        st.session_state[modal_state_key] = False

    # ---- Summary KPI card ----
    with st.container(border=True):
        total_value = get_total_value(df, value_col)

        st.metric(
            label=label,
            value=total_value,
            delta=None,
        )

        if groupby and len(df) > 1:
            if st.button("View by instance", key=f"{metric_id}_btn"):
                st.session_state[modal_state_key] = True
                st.rerun()  # optional, but improves responsiveness

    # ---- Dialog ----
    if st.session_state[modal_state_key]:

        @st.dialog(f"{label} — by instance")
        def _instance_dialog():
            for _, row in df.sort_values(groupby).iterrows():
                st.metric(
                    label=str(row[groupby[0]]),
                    value=row[value_col],
                    delta=row[delta_col] if delta_col else None,
                )

            st.divider()

            if st.button("Close", key=f"{metric_id}_close"):
                st.session_state[modal_state_key] = False
                st.rerun()

        _instance_dialog()
