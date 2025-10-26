from dataikupulse.src import dss_folder
from dataikupulse.src import dss_funcs
from dataikupulse.src import dss_init

import os
import shutil
import pandas as pd

from dataiku.runnables import Runnable, ResultTable

class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.pulse_project_key = plugin_config.get("pulse_project_key", None)
        self.pulse_dataiku_user  = plugin_config.get("pulse_dataiku_user", "admin")
        self.pulse_folder_connection = plugin_config.get("pulse_folder_connection", "filesystem_folders")
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        results = []
        cont = True
        # Get local client and name
        local_client = dss_funcs.build_local_client()
        instance_name = dss_funcs.get_dss_name(local_client)
        project_handle = local_client.get_project(self.pulse_project_key)
        library = project_handle.get_library()
        # Create the folders
        if cont:
            try:
                f = dss_folder.get_local_folder(self, project_handle, "partitioned_data")
                results.append(["Create Folders", True, None])
            except Exception as e:
                results.append(["Create Folders", False, f"An error occurred: {e}"])
                cont = False
        # Get plugin directory
        if cont:
            root_path = local_client.get_instance_info().raw["dataDirPath"]
            source_path = None
            path_install = f"{root_path}/plugins/installed/dataiku-pulse"
            path_dev = f"{root_path}/plugins/dev/dataiku-pulse"
            if os.path.isdir(path_install):
                source_path = path_install
                results.append(["plugin directory found", True, None])
            elif os.path.isdir(path_dev):
                source_path = path_dev
                results.append(["plugin directory found", True, None])
            else:
                results.append(["plugin directory", False, "Cannot find plugin Directory"])
                cont = False
        # -- future release create the CS in admin as well so I control the whole naming
        if cont:
            # todo: create CS in admin settings for users removing a setup step
            print()
        # Create the Code Studio Template
        if cont:
            try:
                found = False
                for cs in project_handle.list_code_studios():
                    if cs.name == "Dataiku Pulse Dashboard":
                        found = True
                        break
                if not found:
                    code_studio = project_handle.create_code_studio(name="Dataiku Pulse Dashboard", template_id="dataiku_pulse_dashboard")
                results.append(["Create Code Studio", True, None])
            except Exception as e:
                results.append(["Create Code Studio", False, f"An error occurred: {e}"])
                cont = False
        # return results
        if results:
            df = pd.DataFrame(results, columns=["step", "result", "message"])
            df = df.astype(str)
            rt = ResultTable()
            n = 1
            for col in df.columns:
                rt.add_column(n, col, "STRING")
                n +=1
            for index, row in df.iterrows():
                rt.add_record(row.tolist())
            return rt
        else:
            raise Exception("Something went wrong")
