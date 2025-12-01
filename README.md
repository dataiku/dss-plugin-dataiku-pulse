# Dataiku PULSE Dashboard and Collector
* Version - 2.2.1

## Scope

This dashboard is designed to give Dataiku Admins insights into the DSS instance.

* Dataiku Insights (API Dataiku)
* Dataiku Usage (Audit Logs)


## Tested Dataiku Versions

1. v2.1/0
    1. v14.2
1. V1.X
    1. v14.1
    1. v14.0
    1. v13.5


## Installation Notes

Due to the web application being built on Streamlit, installation requires a bit of dedicated code use. Hoping this changes in later DSS versions.

1. Plugin
    1. Login as an Administrative account
    1. Migrate to `Waffle::Plugins` and install from GIT: <https://github.com/dataiku/dss-plugin-dataiku-pulse.git>
    1. Build the code-environment, **no containers needed**
    1. After the plugin is installed, switch to the plugin settings page and fill in the information ("EXAMPLE BELOW")
        1. PULSE Dashbaord: This is the main parameter set to house all the base configurations for the application. Create a single PARAM_SET named `primary` (LOWERCASE!) and populate each field.
            1. GitHub Repository Information
                1. Repo: <https://github.com/dataiku/dss-plugin-dataiku-pulse.git>
                1. Branch: `main`
            1. Dashboard Information
                1. Dashboard Project Key: `DATAIKU_PULSE_DASHBOARD`
                1. Dashboard Host URL: Hostname or IP:Port
                1. Dashboard Host API: Admin Level Api Key
                1. BLOB Folder: <Dataiku Connection String name [AWS|Azure|GCS]
            1. Worker Nodes
                1. Worker Node Project Key: `DATAIKU_PULSE_WORKER`
                1. Fill out each host including the local host if you want to track the local host.
                    1. Need both Hostname or IP:Port and Admin level API Key
                    1. For more custom control add a PARAM_SET name specific to the host for the next section
                1. User: User to own/run the scenarios
                1. Ignore Certs: Auto trust https between nodes
                1. Project Data Parallel: Gather Project metadata in parallel
                1. Cores: How many cores to run for project data
        1. (OPTIONAL) Worker Nodes: This will container additional auto information or custom information per host
            1. Create a PARAM_SET matching the name of the worker node PARAM_SET from the previous section
            1. Custom User, Certs, Parallel/Cores
            1. Macro Configuration: PLACE HOLDER -- Coming v2.2
1. Code Studios
    1. Create the template name `dataiku_pulse_dashboard` # this name is important
    1. Setup K8s to run on
    1. Add the `Dataiku Pulse (Streamlit Custom)` block
    1. Disable permissions for users
    1. Build
1. Create the Dataiku PULSE Dashboard project based off 1.4.2 information
    1. Go to Macros
    1. Filter on `Dataiku Pulse: Initialize`
    1. Run `Initialize Dashboard`
    1. Run `Initialize Workers`
    1. Switch to Code Studios page under the Code tab
        1. Click the checkbox and publish as a Web Application (No API for this)
        1. Start the Web Application (Auto-Start)
        1. Nothing may be available at first while the first day cycle needs to run to gather data

## Data Flow Diagrams

![Data Flow Diagram](<images/PULSE Data Flow.svg>)

## Contributors

* Author - Stephen Mazzei
* Email - <Stephen.Mazzei@dataiku.com>
* Special Thanks
  * Development
    * Jordan Burke
    * Ben Bourgeois
    * Jonathan Sill
  * Documentation
    * Rob Harris
  * Project Management
    * Arjun Srivatsa
