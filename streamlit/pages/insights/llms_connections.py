from pages.layouts import dashboard_main
try:
    from dashboards import genai_llm as stock_dashboards
except:
    stock_dashboards = False
custom_dashboards = False

# -----------------------------------------------------------------------------
# Set the category and load the main layout
category = "LLM Connections"
metrics = []
graphs = []
dashboard_main.main(category, stock_dashboards, custom_dashboards, metrics, graphs)