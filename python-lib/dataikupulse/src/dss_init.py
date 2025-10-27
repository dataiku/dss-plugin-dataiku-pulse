import os
import yaml
import json

def load_yaml(path="./scenarios.yaml"):
    script_dir = os.path.dirname(os.path.realpath(__file__))
    try:
        yaml_path = os.path.join(script_dir, path)
        with open(yaml_path, "r") as f:
            config = yaml.safe_load(f)
    except:
        config = {}
    return config

def update_plugin_config(self, plugin_handle):
    settings = plugin_handle.get_settings()
    settings.settings["defaultPermission"] = {"admin": True, "canViewComponents": False}
    settings.settings["excludedFromCDE"] = True
    settings.settings["detailsNotVisible"] = False 
    settings.settings["codeEnvName"] = "plugin_dataiku-pulse_managed"
    settings.settings["config"]["pulse_repo_url"]    = self.pulse_repo_url
    settings.settings["config"]["pulse_repo_branch"] = self.pulse_repo_branch 
    settings.settings["config"]["pulse_project_key"]   = self.pulse_project_key
    settings.settings["config"]["pulse_project_url"]   = self.pulse_project_url
    settings.settings["config"]["pulse_project_api"]   = self.pulse_project_api 
    settings.settings["config"]["pulse_worker_key"]    = self.pulse_worker_key
    settings.settings["config"]["pulse_dataiku_user"]  = self.pulse_dataiku_user 
    settings.settings["config"]["ignore_certs"]       = self.ignore_certs
    settings.settings["config"]["pulse_folder_connection"] = self.pulse_folder_connection
    settings.save()
    return

    
def install_plugin(self, remote_client):
    # Only install if not found, if found and set to update, patch
    pulse_found = False
    for plugin in remote_client.list_plugins():
        if plugin["id"] == "dataiku-pulse":
            pulse_found = True
    if pulse_found and self.update_github:
        plugin_handle = remote_client.get_plugin(plugin_id="dataiku-pulse")
        git_update = plugin_handle.update_from_git(
            repository_url=self.pulse_repo_url,
            checkout=self.pulse_repo_branch
        )
        r = git_update.wait_for_result()
        if not r["success"]:
            raise Exception("Plugin Failed to Update")
        # Update the code-env
        code_env = plugin_handle.update_code_env()
        r = code_env.wait_for_result()
        if r["messages"]["warning"] or r["messages"]["error"] or r["messages"]["fatal"]:
            raise Exception(r["messages"]["messages"])
        # Update the plugin config
        update_plugin_config(self, plugin_handle)
    else:
        plugin_install = remote_client.install_plugin_from_git(
            repository_url=self.pulse_repo_url, checkout=self.pulse_repo_branch, subpath=None
        )
        r = plugin_install.wait_for_result()
        r = plugin_install.get_result()
        if r["messages"]["warning"] or r["messages"]["error"] or r["messages"]["fatal"]:
            raise Exception(r["messages"]["messages"])
        plugin_handle = remote_client.get_plugin(plugin_id="dataiku-pulse")
        # create the code-env
        code_env = plugin_handle.create_code_env()
        r = code_env.wait_for_result()
        r = code_env.get_result()
        if r["messages"]["warning"] or r["messages"]["error"] or r["messages"]["fatal"]:
            raise Exception(r["messages"]["messages"])
        # Update the plugin config
        update_plugin_config(self, plugin_handle)
    
    return


def create_worker(client, pulse_worker_key):
    if pulse_worker_key not in client.list_project_keys():
        project_handle = client.create_project(project_key=pulse_worker_key, name=pulse_worker_key, owner="admin")
    else:
        project_handle = client.get_project(project_key=pulse_worker_key)
    return project_handle


def get_dss_commits(project_handle):
    dataset = project_handle.get_dataset("dss_commits")
    if not dataset.exists():
        dataset = project_handle.create_dataset(
            dataset_name = "dss_commits",
            type = "StatsDB",
            params = {
                'view': 'COMMITS',
                'orderByDate': False,
                'clusterTasks': {},
                'commits': {},
                'jobs': {},
                'scenarioRuns': {},
                'flowActions': {}
            }
        )
        schema = {
            "columns": [
                {"name": "project_key", "type": "string"},
                {"name": "commit_id", "type": "string"},
                {"name": "author", "type": "string"},
                {"name": "timestamp", "type": "bigint"},
                {"name": "added_files", "type": "int"},
                {"name": "added_lines", "type": "int"},
                {"name": "removed_files", "type": "int"},
                {"name": "removed_lines", "type": "int"},
                {"name": "changed_files", "type": "int"},
            ],
            "userModified": True,
        }
        r = dataset.set_schema(schema=schema)
    return


def create_scenarios(self, project_handle):
    # Clear out any old
    for scenario in project_handle.list_scenarios():
        if "data_gather_" in scenario["name"]:
            scenario_handle = project_handle.get_scenario(scenario["id"])
            r = scenario_handle.delete()
    
    # Create the scenarios
    macros = load_yaml()
    for key in macros["macros"]:
        # rebase and setup macro in step
        trigger = json.loads(macros["trigger"])
        step = json.loads(macros["step"])
        step["params"]["runnableType"] = macros["macros"][key]
        # create or connect to scenario
        try:
            scenario_handle = project_handle.get_scenario(scenario_id=key)
            settings = scenario_handle.get_settings()
        except:
            scenario_handle = project_handle.create_scenario(scenario_name=key, type="step_based")
            settings = scenario_handle.get_settings()
        # Run As User
        settings.data["runAsUser"] = self.pulse_dataiku_user
        # Trigger
        del settings.raw_triggers[:]
        settings.raw_triggers.append(trigger)
        # Steps
        del settings.raw_steps[:]
        settings.raw_steps.append(step)
        # Save
        settings.active = True
        settings.save()
        # RUN
        if self.force_scenarios:
            run = scenario_handle.run()
    return