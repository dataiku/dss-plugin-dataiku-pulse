import dataiku
from dataiku.customrecipe import get_recipe_config


def run():
    project_key = dataiku.default_project_key()
    recipe_config = get_recipe_config() or {}
    normalize_silver = bool(recipe_config.get("normalize_silver", True))
    return 

if __name__ == "__main__":
    run()
