import json
import os
from pathlib import Path

import yaml


PULSE_MODULES_DIR = Path(__file__).resolve().parents[1]

def load_yaml(path: str) -> dict:
    yaml_path = PULSE_MODULES_DIR / path
    try:
        with yaml_path.open("r") as f:
            return yaml.safe_load(f) or {}
    except (FileNotFoundError, yaml.YAMLError):
        return {}


def update_plugin_config(self, plugin_handle):
    settings = plugin_handle.get_settings()
    # Dashboard
    param_set = settings.get_parameter_set(parameter_set_name="params-dashboard-instance")
    preset = param_set.get_preset(preset_name="primary")
    if not preset:
        preset = param_set.create_preset(preset_name="primary")
    preset.get_raw()["pluginConfig"] = self.params
    param_set.save()
    return


def update_default_node(self, plugin_handle):
    settings = plugin_handle.get_settings()
    # Node
    param_set = settings.get_parameter_set(parameter_set_name="params-worker-instances")
    preset = param_set.get_preset(preset_name=self.preset_pc_name)
    if not preset:
        preset = param_set.create_preset(preset_name=self.preset_pc_name)
    preset.get_raw()["pluginConfig"] = self.preset_pc
    preset.get_raw()["description"] = "This is automatically generated from the Pulse Init Worker Macro. Do not delete. Update from the Primary Plugin Settings."
    param_set.save()
    return



def install_plugin(self, remote_client):
    # Only install if not found, if found and set to update, patch
    pulse_found = False
    for plugin in remote_client.list_plugins():
        if plugin["id"] == "dataiku-pulse":
            pulse_found = True
    if pulse_found and self.config["update_github"]:
        plugin_handle = remote_client.get_plugin(plugin_id="dataiku-pulse")
        git_update = plugin_handle.update_from_git(
            repository_url = self.params["pulse_repo_url"],
            checkout = self.params["pulse_repo_branch"]
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
            repository_url = self.params["pulse_repo_url"],
            checkout = self.params["pulse_repo_branch"],
            subpath = None
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
    macros = load_yaml(path="./scenarios/worker.yaml")
    for key in macros["macros"]:
        if not self.params["monitor_os"] and key in ["data_gather_diskspace", "data_gather_filesystem"]:
            continue
        # rebase and setup macro in step
        trigger = json.loads(macros["trigger"])
        step = json.loads(macros["step"])
        step["params"]["runnableType"] = macros["macros"][key]
        step["params"]["config"] = {"pulse_primary": {"mode": "PRESET", "name": self.primary_preset_name}}
        # create or connect to scenario
        try:
            scenario_handle = project_handle.get_scenario(scenario_id=key)
            settings = scenario_handle.get_settings()
        except:
            scenario_handle = project_handle.create_scenario(scenario_name=key, type="step_based")
            settings = scenario_handle.get_settings()
        # Run As User
        settings.data["runAsUser"] = self.preset_pc["pulse_dataiku_user"]
        # Trigger
        del settings.raw_triggers[:]
        settings.raw_triggers.append(trigger)
        # Custom -- Trigger Adjustments
        if key == "data_gather_audit_logs":
            adj_trigger = json.loads(macros["audit_trigger"])
            settings.raw_triggers[0]["params"]["repeatFrequency"] = adj_trigger["repeatFrequency"]
            settings.raw_triggers[0]["params"]["frequency"] = adj_trigger["frequency"]
        # Cleanup Scenario Adjustment
        if key == "data_gather_cleanup":
            step = json.loads(macros["cleanup_step"])
        # Steps
        del settings.raw_steps[:]
        settings.raw_steps.append(step)
        # Save
        settings.data["active"] = True
        settings.save()
        # RUN
        if self.config["force_scenarios"]:
            run = scenario_handle.run()
    return

def dashboard_scenrios(self, project_handle):
    # Clear out any old
    for scenario in project_handle.list_scenarios():
        if "gold_data_" in scenario["name"]:
            scenario_handle = project_handle.get_scenario(scenario["id"])
            r = scenario_handle.delete()
    # 
    macros = load_yaml(path="scenarios_yamls/dashboard.yaml")
    for key in macros.get("recipes"):
        # rebase and setup macro in step
        trigger = json.loads(macros["trigger"])
        step = json.loads(macros["step"])
        step['params']['builds'][0]['itemId'] = self.folder_id
        # create or connect to scenario
        try:
            scenario_handle = project_handle.get_scenario(scenario_id=key)
            settings = scenario_handle.get_settings()
        except:
            scenario_handle = project_handle.create_scenario(scenario_name=key, type="step_based")
            settings = scenario_handle.get_settings()
        # Run As User
        settings.data["runAsUser"] = "admin"
        # Trigger
        del settings.raw_triggers[:]
        settings.raw_triggers.append(trigger)
        # Steps
        del settings.raw_steps[:]
        settings.raw_steps.append(step)
        # Save
        settings.data["active"] = True
        settings.save()
    return