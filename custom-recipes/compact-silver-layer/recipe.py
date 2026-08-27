import dataiku
from dataiku.customrecipe import get_plugin_config, get_recipe_config

import os

def get_dss_execution_environment():
    if "KUBERNETES_SERVICE_HOST" in os.environ or os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount'):
        return "Kubernetes Container"
    elif os.path.exists('/.dockerenv'):
        return "Docker Container"
    else:
        return "Local DSS Server"



def run():
    project_key = dataiku.default_project_key()
    
    plugin_config = get_plugin_config() or {"foo": "bar"}
    recipe_config = get_recipe_config() or {"foo": "bar"}
    
    print(f"AHHHHHHH -- {plugin_config}")
    print(f"AHHHHHHH -- {recipe_config}")
    
    env = get_dss_execution_environment()
    print(f"Recipe execution environment: {env}")
    
    return 

if __name__ == "__main__":
    run()
