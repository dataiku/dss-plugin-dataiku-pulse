import plotly.express as px
import streamlit as st

def render_graph(result, key=None):
    df = result["df"]
    meta = result["meta"]

    if key == None:
        key = meta.get("id")

    graph = meta.get("graph", {})
    kind = graph.get("kind", None)

    if kind == "bar":
        fig = px.bar(
            df,
            x=graph["x"],
            y=graph["y"],
            color=graph.get("color", None),
            barmode=graph.get("barmode", "group"),
            labels=graph.get("labels", None),
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_layout(
            bargap=0.15,
            bargroupgap=0.1,
        )
        fig.update_traces(textposition="outside")

    elif kind == "line":
        fig = px.line(
            df,
            x=graph["x"],
            y=graph["y"],
            color=graph.get("color"),
        )

    else:
        return

    # Global styling (applied everywhere)
    fig.update_layout(
        title=meta.get("label", "Label missing from META"),
        legend_title=graph.get("legend_title", graph.get("color", None)),
        xaxis_title=graph.get("x_title", graph["x"]),
        yaxis_title=graph.get("y_title", graph["y"]),
        template="plotly",
        font=dict(size=14),
    )

    # Annotations
    fig.add_annotation(
        text=meta.get("description", "Description missing from META"),
        x=0.5,
        y=1.02,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(size=12, color="gray"),
        align="center",
    )

    # Display
    st.plotly_chart(
        figure_or_data=fig,
        use_container_width=True,
        theme=None,
        key=key,
    )