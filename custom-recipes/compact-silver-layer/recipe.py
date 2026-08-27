import dataiku
from dataiku.customrecipe import get_recipe_config, get_plugin_config

# Recipe parameters (configured per recipe instance)
recipe_config = get_recipe_config() or {}

# Global plugin parameters (configured at the plugin settings level)

# Accessing a plugin parameter safely
global_api_key = plugin_config.get("api_key", "default_val")

def run():
    project_key = dataiku.default_project_key()
    
    recipe_config = get_recipe_config() or {}
    plugin_config = get_plugin_config() or {}
    
    print(f"AHHHHHHH -- {plugin_config}")
    print(f"AHHHHHHH -- {recipe_config}")
    
    
    return 

if __name__ == "__main__":
    run()
