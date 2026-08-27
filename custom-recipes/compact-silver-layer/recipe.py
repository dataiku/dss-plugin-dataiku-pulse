import dataiku
from dataiku.customrecipe import get_recipe_config, get_plugin_config

def run():
    project_key = dataiku.default_project_key()
    
    recipe_config = get_recipe_config() or {}
    plugin_config = get_plugin_config() or {}
    
    print(f"AHHHHHHH -- {recipe_config}")
    print(f"AHHHHHHH -- {plugin_config}")
    
    
    return 

if __name__ == "__main__":
    run()
