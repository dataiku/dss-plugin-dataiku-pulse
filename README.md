# Dataiku PULSE Dashboard and Collector
* Version - 2.1.0

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
        1. Dataiku PULSE Github Repo
            1. <https://github.com/dataiku/dss-plugin-dataiku-pulse.git>
            1. `main`
        1. Dataiku PULSE Dashboard Information
            1. `DATAIKU_PULSE_DASHBOARD`
            1. Hostname or IP:Port
            1. Admin Level Api Key
            1. `DATAIKU_PULSE_WORKER`
            1. Ignore certs if needed, Default is `False`
        1. Dataiku PULSE Worker Information
            1. Fill out each host including the local host if you want to track the local host. Need both Hostname or IP:Port and Admin level API Key
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
