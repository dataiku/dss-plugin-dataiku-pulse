from sage.src import dss_folder
from sage.src import dss_funcs
from sage.src import dss_init

import os
import shutil
import pandas as pd

from dataiku.runnables import Runnable, ResultTable

class MyRunnable(Runnable):
    def __init__(self, project_key, config, plugin_config):
        self.project_key = project_key
        self.config = config
        self.plugin_config = plugin_config
        self.sage_project_key = plugin_config.get("sage_project_key", None)
        self.sage_dataiku_user  = plugin_config.get("sage_dataiku_user", "admin")

        # Set environment variable
        self.sage_folder_connection = plugin_config.get("sage_folder_connection", "filesystem_folders")
        
    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        results = []
        cont = True
        # Get local client and name
        local_client = dss_funcs.build_local_client()
        instance_name = dss_funcs.get_dss_name(local_client)
        project_handle = local_client.get_project(self.sage_project_key)
        library = project_handle.get_library()
        # Create the folders
        if cont:
            try:
                f = dss_folder.get_local_folder(self, project_handle, "partitioned_data")
                f = dss_folder.get_local_folder(self, project_handle, "base_data")
                results.append(["Create Folders", True, None])
            except Exception as e:
                results.append(["Create Folders", False, f"An error occurred: {e}"])
                cont = False
        # Create scenario to build base data 
        if cont:
            try:
                dss_init.create_scenarios(project_handle, "DASHBOARD", self.sage_dataiku_user)
                results.append(["Update Scenarios", True, None])
            except Exception as e:
                cont = False
                results.append(["Update Scenarios", False, e])
        # Get plugin directory
        if cont:
            root_path = local_client.get_instance_info().raw["dataDirPath"]
            source_path = None
            path_install = f"{root_path}/plugins/installed/sage"
            path_dev = f"{root_path}/plugins/dev/sage"
            if os.path.isdir(path_install):
                source_path = path_install
            elif os.path.isdir(path_dev):
                source_path = path_dev
            else:
                results.append(["plugin directory", False, "Cannot find plugin Directory"])
                cont = False
            results.append(["plugin directory", True, None])
        # Get Dashboard library directory
        if cont:
            project_path = f"{root_path}/config/projects/{self.sage_project_key}/lib/python"
            if not os.path.isdir(project_path):
                results.append(["Project Library Confirmed", False, "Cannot find project library"])
                cont = False
            results.append(["Project Library Confirmed", True, None])
        # Delete the current running version
        if cont:
            project_path = f"{root_path}/config/projects/{self.sage_project_key}/lib/python/sage"
            if os.path.exists(project_path) and os.path.isdir(project_path):
                try:
                    shutil.rmtree(project_path)
                    results.append(["Delete Existing", True, None])
                except OSError as e:
                    results.append(["Delete Existing", False, f"Error deleting directory '{project_path}': {e}"])
                    cont = False
            else:
                results.append(["Delete Existing", True, "Initial Setup"])
        # Copy the streamlit application
        if cont:
            try:
                r = shutil.copytree(f"{source_path}/streamlit", project_path)
                results.append(["Copy Streamlit", True, None])
            except Exception as e:
                results.append(["Copy Streamlit", False, f"An error occurred: {e}"])
                cont = False
        # Clean up Library
        if cont:
            try:
                file = library.add_file("python/sage_init.txt")
                file.delete()
                results.append(["Library Refresh", True, None])
            except Exception as e:
                results.append(["Library Refresh", False, f"An error occurred: {e}"])
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
                    if cs.name == "Sage Dashboard":
                        found = True
                        break
                if not found:
                    code_studio = project_handle.create_code_studio(name="Sage Dashboard", template_id="sage")
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
