import plotly.express as px
import streamlit as st


def render_graph(result, key=None):
    df = result["df"]
    meta = result["meta"]

    if df is None or df.empty:
        st.info("No data to display")
        return

    if key is None:
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

    # -------------------------------
    # NEW: Treemap Support
    # -------------------------------
    elif kind == "treemap":
        fig = px.treemap(
            df,
            path=graph.get("path"),         # hierarchy
            values=graph.get("values"),     # numeric size
            color=graph.get("color"),       # optional
            color_discrete_sequence=px.colors.qualitative.Set2,
        )

        fig.update_traces(
            texttemplate="%{label}<br>%{percentEntry:.1%}",
            textfont_size=14
        )

    else:
        return

    # --------------------------------
    # Global styling
    # --------------------------------
    layout_updates = dict(
        title=meta.get("label", "Label missing from META"),
        legend_title=graph.get("legend_title", graph.get("color", None)),
        template="plotly",
        font=dict(size=14),
        margin=dict(t=120),
    )

    # Only apply axis titles for charts that use axes
    if kind in ("bar", "line"):
        layout_updates.update(
            xaxis_title=graph.get("x_title", graph.get("x")),
            yaxis_title=graph.get("y_title", graph.get("y")),
        )

    fig.update_layout(**layout_updates)

    # --------------------------------
    # Description Annotation
    # --------------------------------
    fig.add_annotation(
        text=meta.get("description", "Description missing from META"),
        x=0.5,
        y=1.08,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(size=12, color="gray"),
        align="center",
    )

    # --------------------------------
    # Render
    # --------------------------------
    st.plotly_chart(
        figure_or_data=fig,
        use_container_width=True,
        theme=None,
        key=key,
    )
