def get_query_dict():
    query = {
        "select": [],
        "from":   [],
        "join":   [],
        "where":  [],
        "group":  [],
        "order":  [],
    }
    return query

def get(type):
    FIG = {
        "pass": True,
        "type": type,
        "title": None,
        "data": None
    }
    if type == "plotly":
        FIG["desc"] = ""
        FIG["key"] = "default"
        FIG["on_select"] = "rerun"
        FIG["selection_mode"] = "points"
    elif type == "metric":
        FIG["label"] = None
        FIG["delta"] = None
    elif type == "bar_chart":
        FIG["x"] = None
        FIG["x_label"] = None
        FIG["y"] = None
        FIG["ylabel"] = None
        FIG["horizontal"] = False
        FIG["stack"] = False
    return FIG