# Dataiku Pulse Dashboard

**Version:** 2.7.1

Pulse is an administrative dashboard for **Dataiku DSS** that provides
centralized visibility into platform metadata and usage across one or more
Dataiku instances.

It is designed for **Dataiku Platform Admins, TAMs, and Solution Architects**
who need operational insight into how DSS is being used at scale.

---

## Overview

Pulse collects and presents:

- **Platform metadata** via the Dataiku API
- **Usage and activity metrics** via audit logs
- **Cross-instance insights** from one or more DSS environments

The application is delivered as a **Streamlit dashboard** running in
**Dataiku Code Studios**, backed by **DuckDB** and external blob storage.

---

## Scope

Pulse provides insights into:

- Dataiku instance configuration and metadata
- User activity and usage patterns
- Projects, datasets, recipes, and platform objects
- Multi-instance operational visibility

Pulse does **not** modify customer data or platform state.
It is a read-only analytics and observability layer.

---

## Supported & Tested Versions

| Pulse Version | Dataiku DSS Version |
|--------------|---------------------|
| v2.7 | v14.3 |
| v2.6 | v14.3 |
| v2.5 | v14.3 |
| v2.1 | v14.2 |
| v1.x | v14.0 – v14.1 |

---

## Installation

Pulse installation requires **Dataiku platform admin access** and involves:

- Plugin installation
- Code environment creation
- Code Studio template configuration
- Project and macro initialization

📘 **Full installation guide:**  
See [`docs/installation.md`](docs/Installation Process.md)

📘 **Prerequisites & permissions:**  
See the docs folder for cloud storage and IAM requirements.

---