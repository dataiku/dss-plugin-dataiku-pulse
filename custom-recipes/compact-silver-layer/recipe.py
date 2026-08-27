import dataiku
from dataiku.customrecipe import get_plugin_config, get_recipe_config 

def run():
    project_key = dataiku.default_project_key()
    
    plugin_config = get_plugin_config() or {}
    recipe_config = get_recipe_config() or {"foo": "bar"}
    
    print(f"AHHHHHHH -- {plugin_config}")
    print(f"AHHHHHHH -- {recipe_config}")    
    
    return 

if __name__ == "__main__":
    run()
