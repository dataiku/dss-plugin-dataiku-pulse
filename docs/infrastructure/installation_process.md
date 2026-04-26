# Pulse Installation Guide

This document describes the **full installation process** for deploying
Pulse in a Dataiku environment.

It assumes familiarity with Dataiku administration concepts such as
plugins, code environments, webapps, and API keys.

---

## 1. Required Permissions

### Platform Admin Access

To install Pulse, you must be a **Dataiku Platform Admin** on the
**primary (core) Dataiku instance**.

This is required because Pulse installation involves:

- Installing a Dataiku plugin
- Creating a Code Environment
- Creating and managing Dataiku projects
- Running administrative macros
- (Optional) Installing/updating the plugin on worker instances from the hub

In practice, **full admin access** on the core instance is required.

---

## 2. Multi-Instance API Access

Pulse can collect metadata and usage data from **multiple Dataiku instances**.

For each instance you want to connect:

- You must be a **full admin**  
  **OR**
- An admin must generate an **API key** with sufficient permissions

### API Key Capabilities

API keys must be able to:

- Read metadata
- Read audit and usage logs

These API keys are configured during Pulse setup.

---

## 3. Plugin Installation

1. Log in to Dataiku as an **administrative user**
2. Navigate to **Waffle → Plugins**
3. Install the plugin from Git:

   ```
   https://github.com/dataiku/dss-plugin-dataiku-pulse.git
   ```

4. Build the plugin **code environment**  
   - No containers are required

---

## 4. Plugin Configuration

After installation, open the **Plugin Settings** page.

### Primary Parameter Set

Create a single parameter set named:

```
primary
```

⚠️ The name must be **lowercase**.

Populate the following sections:

#### GitHub Repository
- Repository URL
- Branch (typically `main`)

#### Dashboard Configuration
- Dashboard Project Key  
  ```
  DATAIKU_PULSE_DASHBOARD
  ```
- Dashboard Host URL (hostname or IP:port)
- Dashboard Host API Key (admin-level)
- Blob Storage Connection (AWS, Azure, or GCS)

#### Worker Nodes
- Worker Project Key  
  ```
  DATAIKU_PULSE_WORKER
  ```
- One entry per Dataiku instance:
  - Hostname or IP:port
  - Admin-level API key
- User to own and run scenarios
- Optional settings:
  - Ignore certificates
  - Project data parallelization
  - Core count

### Optional Per-Host Parameter Sets

You may create additional parameter sets for host-specific overrides:

- Custom user
- Certificate behavior
- Parallelism / core settings

---

## 5. Webapp Setup

Pulse ships as a **DSS Webapp** (React frontend + Flask backend) packaged inside the plugin.

1. Create the **Pulse Dashboard Project**
2. In that project, create a new **Webapp**
3. Select the plugin webapp:
   - **Dataiku Pulse Dashboard**

---

## 6. Project Initialization

1. Navigate to **Macros** in the Pulse Dashboard Project
2. Filter on:

   ```
   Dataiku PULSE Insights: Initialize
   ```

3. Run:
   - **Initialize Dashboard** (creates `partitioned_data`, `gold_data`, `create_gold_tables`, scenario `gold_data_refresh`)
   - **Initialize Worker Host(s)** (installs/updates plugin on workers, creates worker project, dataset, variables, scenarios)

⚠️ The dashboard may initially appear empty while the first
collection cycle runs and GOLD is built.

---

## 7. Blob Storage & DuckDB

Pulse stores all collected data in **external blob storage**
and loads final tables into **DuckDB**.

- AWS and Azure work out of the box using standard credentials
- GCS requires additional IAM configuration

See `docs/gcs-auth.md` for GCS-specific setup and validation.

---

## 8. Installation Complete

Once the initial collection cycle completes, the Pulse dashboard
will begin displaying insights and usage metrics.

At this point:
- Core installation is complete
- Additional customization and extensions can be added as needed

