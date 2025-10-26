from sage.pages.layouts import dashboard_main
try:
    from sage.dashboards import datasets as stock_dashboards
except:
    stock_dashboards = False
custom_dashboards = False

# -----------------------------------------------------------------------------
# Set the category and load the main layout
category = "Datasets"
metrics = []
graphs = []
dashboard_main.main(category, stock_dashboards, custom_dashboards, metrics, graphs)