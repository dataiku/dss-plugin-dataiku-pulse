from sage.pages.layouts import dashboard_main
try:
    from sage.dashboards import users as stock_dashboards
except:
    stock_dashboards = False
custom_dashboards = False

# -----------------------------------------------------------------------------
# Set the category and load the main layout
category = "Users"
metrics = [
    "Metrics Total Users",
    "Metrics All Users With Access",
    "Metrics Users With Access No Readers",
    "Metrics New Users 30 Days",
]
graphs = [
    "Graphs Rolling Login Users",
    "Graphs Rolling Developer Users",
    "Graphs All User Attrition",
    "Graphs Developer User Attrition",
    "Graphs Users By Creation Date M",
    "Graphs User Profile Counts",
    "Graphs Top Git Users",
]
dashboard_main.main(category, stock_dashboards, custom_dashboards, [], [])