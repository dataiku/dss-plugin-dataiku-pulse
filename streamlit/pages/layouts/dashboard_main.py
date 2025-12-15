import streamlit as st

from src import dss_streamlit
from pages.layouts import dashboard_metrics
from pages.layouts import dashboard_graphs


# ------------------------------------------------------------------------------------
# Main function
def main(category, stock_insights, custom_insights, metrics = [], graphs = []):
    data_category = category.lower().replace(" ", "_")
    st.set_page_config(initial_sidebar_state="expanded")
    st.set_page_config(layout="wide")

    # Setup base sidebar
    with st.sidebar:
        with st.container(border=True):
            genre = st.selectbox(
                label = "## Select an Insight",
                options = ["Metrics", "Graphs"], #, "Explore DataFrame"],
                index = 0
            )

    # Load the INSIGHTS information
    display_data = []
    modules = {}

    if stock_insights:
        tmp_modules, tmp_display_data = dss_streamlit.collect_display_data(stock_insights)
        modules = modules | tmp_modules
        display_data += tmp_display_data
    if custom_insights:
        tmp_display_data, tmp_d = dss_streamlit.collect_display_data(custom_insights)
        modules = modules | tmp_modules
        display_data += tmp_display_data
    display_data = sorted(display_data)

    # Display
    st.header(f"Dataiku {category}: {genre}")
    if genre == "Metrics":
        dashboard_metrics.main(data_category, display_data, modules, metrics)
    elif genre == "Graphs":
        dashboard_graphs.main(data_category, display_data, modules, graphs)
    #elif genre == "Explore DataFrame":
    #    dashboard_explore.main(data_category, display_data, modules)
    return