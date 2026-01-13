import streamlit as st
from components import layout_usage_instances, layout_usage_actors

st.set_page_config(initial_sidebar_state="expanded")
st.set_page_config(layout="wide")

def base_display(tab, category):
    # Setup base sidebar
    with st.sidebar:
        with st.container(border=True):
            genre = st.selectbox(
                label = "## Select an Insight",
                options = ["Instances", "Actors"],
                index = 0
            )

    # Display
    data_category = category.lower().replace(" ", "_")
    st.header(f"Dataiku {category}")

    # Select Page to load
    if genre == "Instances":
        layout_usage_instances.display(tab, data_category)
    elif genre == "Actors":
        layout_usage_actors.display(tab, data_category)
    return