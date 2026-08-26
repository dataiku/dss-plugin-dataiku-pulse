# Install

This guide describes the v3 installation model for Dataiku Pulse.

Pulse v3 is installed as a Dataiku plugin and deployed using a **hub-and-spoke model**:

- the **hub** hosts the shared Pulse dashboard project, centralized storage flow, and web application
- one or more **spokes** provide worker-side collection from local Dataiku instances

This document covers both:

- **installation requirements**
- **installation and initialization steps**

## Requirements

Before installing Pulse, make sure the target environment satisfies the following prerequisites.

### Platform access

For the initial Pulse installation, you must be a **Dataiku Platform Admin** on the primary hub instance.

This is required because the setup involves platform-level actions such as:

- installing the Pulse plugin
- building the plugin code environment
- configuring plugin settings
- creating and managing the Pulse hub project
- creating the Pulse web application
- running initialization macros
- optionally provisioning worker-side setup across connected instances

In practice, the initial install should be performed by a user with full administrative access on the hub instance.

### Access to connected instances

Pulse can collect metadata and usage data from multiple Dataiku instances.

For each spoke instance you want to connect, you need one of the following:

- **full admin access** on that instance
- an **admin-issued API key** with sufficient permissions

These credentials are used so the hub can configure and coordinate worker-side collection.

At minimum, connected-instance access must allow Pulse to:

- read instance and project metadata
- read audit and usage information
- perform the setup needed for worker-side Pulse operation

### Blob storage connection

Pulse stores collected outputs in shared storage and uses those files to support downstream modeling and analytics.

You need a blob storage connection that is:

- accessible from the Pulse runtime environment
- configured in Dataiku
- allowed to read and write Pulse-managed data

This shared storage is used for the centralized ingestion path that sits between spoke-side collection and hub-side modeling.

### Parquet runtime support

Pulse relies on Parquet files in its data flow.

Because of that, customers must ensure that the **Hadoop/Spark add-on tarball packages** matching their Dataiku release are installed in the environment.

This is an important runtime prerequisite for Pulse v3 and should be validated before installation, especially in customer-managed deployments.

### Code environment readiness

Pulse requires its plugin code environment to build successfully on the hub instance.

Before proceeding, confirm that:

- the plugin code environment can be created
- the environment has access to the dependencies required by the plugin
- the instance can use the storage and file-format capabilities Pulse depends on

### Cloud storage notes

Pulse uses shared storage and Parquet-backed data exchanges across its ingestion and modeling flow.

General expectations:

- **AWS** and **Azure** typically work with their supported credential models when the Dataiku runtime has proper access
- **GCS** may require additional IAM and runtime credential validation depending on the customer environment

If the deployment uses GCS, validate access before installation so Pulse can read and write its managed files successfully.

## Installation Steps

### 1. Install the plugin

1. Log in to Dataiku as an administrative user on the hub (primary) instance.
2. Navigate to **Waffle → Plugins**.
3. Install the Pulse plugin from the appropriate source (either from GitHub or Local).
4. Build the plugin code environment (Can support containers or local only).

At this point, the Pulse plugin is available to the instance, but the dashboard project and worker topology are not yet initialized.

### 2. Configure plugin settings

After the plugin is installed, open the **Plugin Settings** page.

Create and populate the required parameter set used by the Pulse deployment.

### Primary parameter set `PULSE Dashboard`

While the name does not "matter", we typically recommend something simple such as `primary`, create a parameter set named:

```text
primary
```

Populate the configuration needed for:

- GitHub Repository Information
  - Either Internal URL or Public (Dataiku) URL
  - Version/Branch name
- Dashboard (Hub) Information
  - The Hub Project Key
  - The Hub URL host information
  - The Hub Admin level API Key
  - Shared blob storage
  - If GCS, HMAC information
- Worker (Spoke) Information
  - The Worker Project Key
  - Worker Hosts (Can add 1 or Many)
    - URL per hosts (can/should include Hub host)
    - Admin level API Key per host
    - Node Classification (`Designer` or `Automation`)
    - Parameter Set Name (Can be null, ask TAM for custom host setup)
  - Dataiku user to own Projects and Scenarios
  - Ignore SSL Certs for API calls
  - Run Project information collection in parallel, how many cores
- Debug
  - Most items can be ignored
  - If not using an Event-Server, you may want to select to backup Audit Logs

### 3. Create the Pulse dashboard project

Create the Dataiku project that will host the Pulse hub experience (making sure to match the project key used in the plugin settings).

This project becomes the main Pulse hub project and is where you will:

- initialize dashboard-side objects
- run Pulse macros
- host the Pulse web application
- manage centralized analytics outputs

### 4. Run Pulse initialization macros

After the hub project is in place, initialize Pulse from the hub project macros.

From the "3 parallel dots", select Macros, then look for the `Dataiku Pulse Insights: initialize` category.

This setup is responsible for establishing the Pulse operating model, including:

- Initialize Dashboard
  - typically ran only once during setup
  - hub-side dashboard and storage initialization
  - creation of the assets needed for collection and refresh flows
- Initialize Worker Host(s)
  - Ran each time new hosts or versions are updated
  - worker-side project setup on connected instances
  - `Designer` workers create the worker project directly via DSS APIs
  - `Automation` workers bootstrap from `resource/dataiku_pulse_worker_bundle_skeleton_v1.zip`, then activate the imported bundle before Pulse creates datasets, variables, and scenarios
  - Can force GitHub updates, skip GitHub, or force run scenarios now

Depending on the environment, the first full collection and modeling cycle may take some time to complete.

### 5. Validate worker and hub setup

After initialization, validate that:

- the hub project was created and configured as expected
- worker-side setup exists on each connected spoke instance
- collection outputs begin landing successfully (if enabled)

### 6. Create the Pulse web application

Pulse v3 the dashboard is now packaged directly inside the plugin as the Pulse web application.

To add it:

1. Open the Pulse hub project.
2. Go to the **Webapps** tab.
3. Select **NEW WEBAPP**.
4. Choose **Visual Webapp**.
5. Select **Dataiku Pulse Dashboard**.

This creates the Pulse dashboard webapp directly from the plugin-provided object.

### 7. Confirm end-to-end readiness

Before declaring the installation complete, verify the full Pulse chain:

- plugin installed and code environment built
- plugin settings configured
- Pulse hub project created
- Pulse web application created from **Visual Web App → Dataiku Pulse Dashboard**
- initialization macros completed
- connected instances reachable with the configured access
- shared storage working
- Parquet support available through the required Hadoop/Spark add-on tarball packages

Once those checks pass and the first data refresh completes, Pulse is ready for use.

## Worker cursor defaults

During worker initialization, if the worker project does not already contain the Pulse cursor variables, Pulse creates:

- `local.projects_delta`
- `local.audit_log_delta`

Both values are initialized to **3 calendar months before the current UTC time**.

Example:

- if initialization runs on `2026-07-02`, missing cursors initialize to `2026-04-02` with the current UTC time component preserved

## Notes for v3

A few installation expectations changed in v3 compared with older material.

### No separate Code Studio Streamlit setup

Older setup patterns may reference manually created Code Studio or Streamlit dashboard objects.

That is no longer part of the v3 installation flow.

The supported v3 approach is to create the dashboard directly as a plugin-provided web application from the project’s **Web Applications** tab.

### Webapp is plugin-packaged

The React and Flask dashboard experience is now bundled with the Pulse plugin.

Operationally, this means the dashboard is deployed as a plugin webapp rather than as a separate custom application object outside the plugin flow.

### Parquet support is a hard prerequisite

Because Pulse v3 relies on Parquet-backed processing, the Hadoop/Spark add-on tarball packages that ship with each Dataiku release must be present in the environment.

This should be treated as part of the baseline installation checklist.

## Summary

To install Pulse v3 successfully:

- use a hub instance where you have platform-admin access
- install the plugin and build its code environment
- configure hub, storage, and spoke connection settings
- create the Pulse hub project
- create the dashboard via **Web Applications → New → Visual Web App → Dataiku Pulse Dashboard**
- run the initialization macros
- confirm connected-instance access, shared storage, and Parquet runtime readiness

Once initialized, Pulse begins operating as a centralized hub with worker-side collection across connected Dataiku instances.

## DuckDB extensions (offline-friendly loading)

Pulse pins DuckDB to a specific version and needs the `httpfs` (AWS/GCS) or
`azure` extension depending on the storage connection. Extensions load through
a three-step fallback (`shared_duckdb/extensions.py`):

1. **Network install** — `INSTALL <ext>` from the DuckDB extension repository.
2. **Local cache** — `LOAD <ext>` from `~/.duckdb/extensions/...` if a previous
   install populated it.
3. **Bundled binary** — loaded from
   `resource/duckdb_extensions/<duckdb_version>/<platform>/<ext>.duckdb_extension`,
   shipped with the plugin for the pinned DuckDB version on `linux_amd64`.

In DSS runtimes, the bundled-extension fallback resolves the plugin resource
directory through the native plugin APIs backed by
`DKU_CUSTOM_RESOURCE_FOLDER`:

- webapps: `dataiku.customwebapp.get_webapp_resource()`
- recipes: `dataiku.customrecipe.get_recipe_resource()`

Outside DSS (for local development and unit tests), Pulse falls back to the
repo layout `dataiku-pulse/resource/`. Pulse never searches recursively for a
binary and never crosses DuckDB versions or platforms when selecting a bundled
extension.

If all three fail, the error names the exact expected bundled path and the
resource-resolution stage that failed (resource directory, `duckdb_extensions`
directory, version/platform directory, or specific extension file), plus the
version/platform matrix actually shipped when available. On air-gapped
instances with a platform outside the bundled matrix, pre-populate the DuckDB
extension cache or add the matching bundled binary.

The DuckDB version is pinned in lockstep between `code-env/python/spec/requirements.txt`
and `tests/requirements-dev.txt`; when bumping it, also re-vendor the bundled
extensions for the new version.


## GOLD Export Troubleshooting

For troubleshooting missing GOLD outputs, validate all three layers in order:

1. Confirm the recipe built the DuckDB tables and logged non-zero row counts where expected.
2. Confirm the recipe logged the unload destination and method for the affected tables.
3. Confirm the managed folder shows the resulting objects via `dataiku.Folder(...).list_paths_in_partition()`.

For partitioned event facts such as `fact_dev_activity_events` and `fact_object_activity_events`, successful unload logs alone are not sufficient proof of visible output; always verify the managed-folder listing after a run.
