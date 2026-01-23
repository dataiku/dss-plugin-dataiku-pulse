import pandas as pd
from dataikupulse.src import dss_funcs


def add_columns(df, column_to_move, target_column):
    moved_column = df.pop(column_to_move)
    target_index = df.columns.get_loc(target_column)
    df.insert(target_index + 1, column_to_move, moved_column)
    return df


def main(self, project_handle, client_d = {}):
    if not project_handle.list_recipes():
        return pd.DataFrame()
    
    # Get project level python code environment
    project_python_env = project_handle.get_settings().settings["settings"]["codeEnvs"]["python"]["mode"]
    if project_python_env == "USE_BUILTIN_MODE":
        python_env_name = "USE_BUILTIN_MODE"
    elif project_python_env == "INHERIT":
        python_env_name = client_d["python_env_name"]
    else:
        python_env_name = project_handle.get_settings().settings["settings"]["codeEnvs"]["python"]["envName"]
        
    # Get project level R code environment
    project_r_env = project_handle.get_settings().settings["settings"]["codeEnvs"]["r"]["mode"]
    if project_r_env == "USE_BUILTIN_MODE":
        r_env_name = "USE_BUILTIN_MODE"
    elif project_r_env == "INHERIT":
        r_env_name = client_d["r_env_name"]
    else:
        r_env_name = project_handle.get_settings().settings["settings"]["codeEnvs"]["r"]["envName"]      
        
    # Get project level CODE_ENV environment
    project_container_env = project_handle.get_settings().settings["settings"]["container"]["containerMode"]
    if project_container_env == "NONE":
        container_env_name = "DSS_LOCAL"
    elif project_container_env == "INHERIT":
        container_env_name = client_d["container_env_name"]
    else:
        container_env_name = project_handle.get_settings().settings["settings"]["container"]["containerConf"]

    # Build base df
    prefix = "recipes_"
    df = pd.json_normalize(project_handle.list_recipes()).add_prefix(prefix)
        
    # Project Key
    df = dss_funcs.rename_and_move_first(df, f"{prefix}projectKey", "project_key")
    df.columns = df.columns.str.replace(".", "_", regex=False)
    
    # Get layer 2 information
    for row in df.itertuples():
        recipes_name = getattr(row, "recipes_name")
        recipes_type = getattr(row, "recipes_type")
        recipe_handle = project_handle.get_recipe(recipes_name)
        # Recipe Engine Better Details
        try:
            recipe_engine_type = recipe_handle.get_status().get_selected_engine_details()["type"]
            recipe_engine_label = recipe_handle.get_status().get_selected_engine_details()["label"]
            recipe_engine_recommended = recipe_handle.get_status().get_selected_engine_details()["recommended"]
        except:
            recipe_engine_type = "UNKNOWN"
            recipe_engine_label = "UNKOWN"
            recipe_engine_recommended = False
        df.loc[df["recipes_name"] == recipes_name, "recipes_params_engineType"] = recipe_engine_type
        df.loc[df["recipes_name"] == recipes_name, "recipes_params_engineLabel"] = recipe_engine_label
        df.loc[df["recipes_name"] == recipes_name, "recipes_params_engineRecommended"] = recipe_engine_recommended
        
        # Individual Objects
        if getattr(row, "recipes_params_containerSelection_containerMode", False) == "INHERIT":
            df.loc[df["recipes_name"] == recipes_name, "recipes_params_containerSelection_containerConf"] = container_env_name
        
        if recipes_type == "python":
            recipe_code_env_mode = recipe_handle.get_settings().data["recipe"]["params"]["envSelection"]["envMode"]
            if recipe_code_env_mode == "USE_BUILTIN_MODE":
                recipe_code_env_name = "USE_BUILTIN_MODE"  
            elif recipe_code_env_mode == "INHERIT":
                recipe_code_env_name = python_env_name
            else:
                recipe_code_env_name = recipe_handle.get_settings().data["recipe"]["params"]["envSelection"]["envName"]
            df.loc[df["recipes_name"] == recipes_name, "recipes_params_envSelection_envName"] = recipe_code_env_name
        if recipes_type == "R":
            recipe_code_env_mode = recipe_handle.get_settings().data["recipe"]["params"]["envSelection"]["envMode"]
            if recipe_code_env_mode == "USE_BUILTIN_MODE":
                recipe_code_env_name = "USE_BUILTIN_MODE"  
            elif recipe_code_env_mode == "INHERIT":
                recipe_code_env_name = r_env_name
            else:
                recipe_code_env_name = recipe_handle.get_settings().data["recipe"]["params"]["envSelection"]["envName"]
            df.loc[df["recipes_name"] == recipes_name, "recipes_params_envSelection_envName"] = recipe_code_env_name
        if recipe_engine_type == "SPARK":
            sparkConfig = {}
            try:
                sparkConfig = recipe_handle.get_status().data["engineParams"]["sparkSQL"]["sparkConfig"]
            except:
                try:
                    sparkConfig = recipe_handle.get_settings().data["recipe"]["params"]["sparkConfig"]
                except:
                    pass
            if sparkConfig:
                df.loc[df["recipes_name"] == recipes_name, "recipes_params_sparkConf"] = sparkConfig.get("inheritConf", "None")
                df.loc[df["recipes_name"] == recipes_name, "recipes_params_sparkConfMods"] = False
                if sparkConfig.get("conf", []):
                    df.loc[df["recipes_name"] == recipes_name, "recipes_params_sparkConf"] = True
                
        # Check for LLMs
        try:
            llm_model = recipe_handle.get_settings().get_json_payload()["llmId"]
        except:
            llm_model = ""
        df.loc[df["recipes_name"] == recipes_name, "recipes_params_llmId"] = llm_model

    #df = dss_funcs._trim_raw_df_by_last_modified(df, last_modified_col="recipes_versionTag_lastModifiedOn", last_update=self.last_update)
    return df