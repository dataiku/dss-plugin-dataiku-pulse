# Pulse – Installation Requirements

This document outlines the **minimum requirements and permissions** needed to install and operate **Pulse** in a Dataiku environment. It is intended for **Dataiku platform admins, TAMs, and solution architects** supporting customer deployments.

---

## 1. Core Dataiku Requirements (Initial Install)

### Required Role

For the initial Pulse installation, you must be a:

- **Dataiku Platform Admin** on the *primary (core) Dataiku instance*

This is required because Pulse installation involves **platform‑level configuration**, not just project‑level setup.

### Why Platform Admin Is Required

Pulse requires permissions to:

- Install and configure a **Dataiku plugin**
- Create and manage a **Code Environment**
- Create a **Webapp** from the Pulse plugin
- Create and manage **Dataiku projects** (Pulse Dashboard + Worker projects)
- Configure plugin settings and global parameters

In practice, this means **full admin access** on the main instance is required for the initial install.

---

## 2. Multi‑Instance Access (API Permissions)

Pulse can collect metadata and usage information from **multiple Dataiku instances**.

To enable this:

- You must be a **full admin** on each instance
  **OR**
- An admin must generate and provide **API keys** for each instance

### API Key Requirements

- API keys must have permissions to:
  - Read metadata
  - Read audit / usage information
- API keys are configured during Pulse setup

Without admin‑level API access, Pulse will not be able to collect data from remote instances.

---

## 3. Blob Storage Requirements

Pulse stores all collected data in **external blob storage** (RAW → SILVER → GOLD).

### Required Capabilities

- A **blob storage connection** accessible from Dataiku
- Write access to the storage location
- Admin‑level permissions on the connection

This storage is used for:
- Raw collected data
- Cleansed and normalized SILVER data
- Final GOLD tables loaded into DuckDB

Because Pulse manages its own datasets and lifecycle, this connection must allow **read/write access**.

---

## 4. DuckDB + Blob Storage Compatibility

Pulse uses **DuckDB** to load and query GOLD tables directly from blob storage.

### AWS & Azure

From testing across **AWS and Azure** environments:

- Access via access keys, environment variables, or managed identity works **out of the box**
- DuckDB + blob storage integration works as expected
- No additional configuration is typically required beyond correct credentials

Once credentials are available to the runtime environment, Pulse can load and query data successfully.

---

## 5. Google Cloud Storage (GCS) – Special Considerations

GCS requires additional attention compared to AWS and Azure.

Pulse accesses GCS via **fsspec**, which relies on **Google Application Default Credentials (ADC)**.

### Why `fsspec` Is Required

Pulse uses:

- `fsspec.filesystem("gcs")`

This provides a filesystem abstraction that allows DuckDB and Python to:

- List objects in GCS buckets
- Read Parquet files directly
- Avoid embedding credentials in code

Authentication is resolved automatically using the identity of the runtime (for example, a Kubernetes service account).

---

## 6. Required GCS IAM Permissions

At minimum, the service account running Pulse must have:

- **`roles/storage.objectViewer`** on the target GCS bucket

This grants:
- Object listing
- Object read access

No bucket or project admin permissions are required.

---

## 7. Quick GCS Permission Tests (Recommended)

Before running Pulse, you can validate GCS access using a **lightweight Python test** from a Dataiku Python notebook or Kubernetes‑backed notebook.

### Test 1: List Bucket Contents

```python
import fsspec

fs = fsspec.filesystem("gcs")
fs.ls("your-bucket-name")
```

**Expected result:**
- A list of objects or prefixes in the bucket

**Failure indicates:**
- Missing IAM permissions
- Incorrect service account attachment
- ADC configuration issue

---

### Test 2: Read a File

```python
import fsspec

fs = fsspec.filesystem("gcs")
with fs.open("your-bucket-name/path/to/file.parquet", "rb") as f:
    f.read(1)
```

**Expected result:**
- No error

This confirms **object‑level read access**, which is required for DuckDB and Pulse.

---

## 8. Summary

To successfully install and run Pulse:

- Full **Dataiku platform admin** access is required on the core instance
- Admin or admin‑issued **API keys** are required for all connected instances
- An **admin‑level blob storage connection** is required
- AWS and Azure work out of the box with supported auth methods
- GCS requires correct **ADC‑based IAM permissions** and validation via `fsspec`

Completing these prerequisites ensures a smooth Pulse installation and reliable data collection.

