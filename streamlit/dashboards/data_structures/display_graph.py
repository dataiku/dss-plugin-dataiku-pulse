import streamlit as st

def main(FIG):
    if not FIG or "pass" not in FIG or not FIG["pass"]:
        return

    # Display a Title
    if "title" in FIG and FIG["title"]:
        st.title(FIG["title"])

    # Start of Graphing / Charting
    if FIG["type"] == "plotly": # PLOTLY
        fig = FIG["data"]
        if FIG["desc"]:
            st.markdown(FIG["desc"])

            #fig.add_annotation(
            #    text=FIG["desc"],
            #    xref="paper", yref="paper",
            #    x=0.5, y=-0.25,
            #    showarrow=False,
            #    font=dict(size=12, color="gray")
            #)
        st.plotly_chart(
            fig, use_container_width=True, theme=None,
            key=FIG["key"], on_select=FIG["on_select"], selection_mode=FIG["selection_mode"]
        )
    
    else: # basic Streamlit
        if FIG["type"] == "metric":
            st.metric(
                FIG["label"], FIG["data"], delta=FIG["delta"], delta_color="normal",
                help=None, label_visibility="visible", border=False, width="content"
            )

        elif FIG["type"] == "bar_chart":
            st.bar_chart(
                FIG["data"], x=FIG["x"], y=FIG["y"], x_label=FIG["x_label"], y_label=FIG["y_label"],
                color=None, horizontal=FIG["horizontal"], stack=FIG["stack"], width=None, height=None, use_container_width=True
            )
    return