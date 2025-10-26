import streamlit as st

from sage.src import dss_streamlit
from sage.dashboards.data_structures import display_graph


# ------------------------------------------------------------------------------------
def main(data_category, display_data, modules, metrics):
    # load only the metric dashboards
    dashboards = metrics
    if not dashboards:
        for value in display_data:
            if value.split(" ")[0].lower() == "metrics":
                dashboards.append(value)
        if not dashboards:
            st.error(f"No {data_category} Metrics to display.")
            return

    # Display
    for key in dashboards:
        with st.container(border=True):
            module_name = modules[key][0]
            fp = modules[key][1]
            FIGS = dss_streamlit.load_insights(module_name, fp)
            if isinstance(FIGS, list):
                ncol = len(FIGS)
                cols = st.columns(ncol, gap="small", border=True)
                for i in range(ncol):
                    with cols[i]:
                        display_graph.main(FIGS[i])
            else:
                FIG = FIGS       
                display_graph.main(FIG)
                
    return