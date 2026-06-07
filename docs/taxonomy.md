# Taxonomy

Pulse v3 presents the Dataiku estate through a webapp-oriented taxonomy built around three ideas:

- **Assets**: foundational project objects that teams build with
- **Products**: deliverables and user-facing outputs that teams publish, expose, or operationalize
- **Capabilities**: high-level functional groupings used in the Development Activity experience

This page documents the taxonomy as it is **shown and grouped in the Pulse web application**. It does **not** describe raw audit log semantics or `msgType` mappings.

## Taxonomy Goals

The Pulse taxonomy is designed to make the UI understandable to business and platform audiences.

In practice, that means the webapp uses a stable vocabulary to answer three different questions:

- **What platform objects exist?** → shown through **Assets** and **Products**
- **What kinds of things are teams creating or maintaining?** → shown through object types and catalog views
- **What areas of the platform are users working in?** → shown through **Capabilities** and their categories

The taxonomy is therefore a **presentation model** for Pulse v3. It turns low-level platform metadata into consistent user-facing groupings.

## How the Webapp Organizes the Experience

In the current v3 webapp, the taxonomy appears primarily under the **Build** section.

The main groupings are:

- **Build → Assets**: inventory and metrics for foundational DSS objects
- **Build → Products**: inventory and metrics for published or consumable outputs
- **Build → Development Activity**: capability and category rollups for platform activity

These groupings are intentionally distinct:

- Assets answer: **what teams are building with**
- Products answer: **what teams are delivering or exposing**
- Capabilities answer: **which platform domains users are working in**

## Assets

In Pulse v3, **Assets** represent core building blocks inside Dataiku projects.

They are generally the objects that structure project work, data pipelines, orchestration, and knowledge organization. In the webapp, Assets are treated as the inventory of core project artifacts.

### Asset types shown by the webapp

The current v3 asset taxonomy includes these asset families:

- **Analyses**
- **Projects**
- **Datasets**
- **Knowledge Banks**
- **Managed Folders**
- **Recipes**
- **Scenarios**
- **SQL Notebooks**

### How to interpret Assets in the UI

Assets are meant to reflect the working components of a DSS project.

Examples of how users should read them:

- **Projects** represent top-level collaboration spaces
- **Datasets** represent core data objects used across flows and analysis
- **Recipes** represent transformation logic and flow-building steps
- **Scenarios** represent orchestration and automation assets within projects
- **Managed Folders** represent file-oriented project storage assets
- **SQL Notebooks** represent interactive SQL work artifacts
- **Analyses** represent exploratory analytical workspaces
- **Knowledge Banks** represent retrieval-oriented knowledge assets

The important idea is not whether an object is technically editable or executable, but whether the webapp presents it as part of the platform’s underlying build inventory.

## Products

In Pulse v3, **Products** represent outputs that are intended to be exposed, consumed, shared, served, or operationalized.

Where Assets describe the working estate, Products describe the delivery estate.

### Product types shown by the webapp

The current v3 product taxonomy includes these product families:

- **Agent Tools**
- **API Services**
- **API Endpoints**
- **Agents**
- **Dashboards**
- **Insights**
- **Retrieval Augmented LLMs**
- **Saved Models**
- **Web Applications**
- **Dataiku Applications**

### How to interpret Products in the UI

Products are grouped separately because they are closer to what downstream users, business stakeholders, or operational consumers experience.

Examples of how users should read them:

- **API Services** and **API Endpoints** represent externally callable service surfaces
- **Agents** and **Agent Tools** represent GenAI-enabled or agentic deliverables
- **Dashboards** and **Insights** represent analytic outputs intended for viewing or sharing
- **Web Applications** and **Dataiku Applications** represent interactive end-user experiences
- **Saved Models** represent deployable modeling outputs
- **Retrieval Augmented LLMs** represent operational GenAI solution objects

This distinction helps the webapp separate **platform components** from **consumable outcomes**.

## Why Assets and Products Are Separate

Pulse v3 intentionally does not place all DSS object types into one flat inventory.

The split exists because the webapp is trying to represent two different perspectives:

- the **construction layer** of the platform
- the **delivery layer** of the platform

A dataset, recipe, or scenario is typically part of how teams build solutions.
A dashboard, API, model, or application is typically part of what teams deliver.

This separation improves the webapp experience in several ways:

- it makes catalog views easier to scan
- it gives more meaningful KPIs for each object family
- it avoids mixing internal project mechanics with outward-facing deliverables
- it aligns the Pulse narrative with how platform teams describe adoption and value

## Capabilities

In Pulse v3, **Capabilities** are the top-level functional buckets used in **Build → Development Activity**.

They are not object inventories. Instead, they are the webapp’s way of grouping development activity into recognizable areas of the Dataiku platform.

### Capability groups shown by the webapp

The current v3 capability display names are:

- **Advanced Analytics & ML**
- **APIs & Integration**
- **Applications & Delivery**
- **Automation & Orchestration**
- **Data Engineering**
- **Project Maintenance**
- **GenAI & LLM**
- **Uncategorized**

These are user-facing labels used to summarize activity in broad platform terms.

## Capability Categories

Within each capability, the webapp presents one or more categories. These categories are the labels users see when activity is grouped more specifically.

### Advanced Analytics & ML

Categories currently shown:

- **Statistics & Analytics**
- **Machine Learning & Operations**

This capability captures work associated with advanced analytical and machine learning workflows as presented by the webapp.

### APIs & Integration

Categories currently shown:

- **API Services**

This capability covers API-oriented work as surfaced in the UI taxonomy.

### Applications & Delivery

Categories currently shown:

- **Application Designer**
- **Web Applications**

This capability represents user-facing app and delivery experiences.

### Automation & Orchestration

Categories currently shown:

- **Automation**
- **Deployer**
- **Scenarios**

This capability groups the operational automation layer of the platform.

### Data Engineering

Categories currently shown:

- **Coding**
- **Misc Recipes**
- **Visual Recipes**
- **Prepare**

This capability captures data flow construction and engineering-oriented work.

### Project Maintenance

Categories currently shown:

- **Projects**
- **Flow**
- **Datasets**
- **Folders**
- **Wiki / Articles / Discussions**
- **Git**

This capability represents day-to-day project upkeep, organization, and collaborative maintenance.

### GenAI & LLM

Categories currently shown:

- **Generative AI & LLM**

This capability groups generative AI activity into a single top-level webapp category in the current v3 taxonomy.

### Uncategorized

Categories currently shown:

- **Administration**
- **Containers**
- **Governance**
- **Plugins**
- **Reading & Listing**
- **User Maintenance**
- **Other**
- **Dataiku Category**

The **Uncategorized** capability is a catch-all bucket for labels that are not yet positioned in a more opinionated capability family.

## What the User Actually Sees

From a user perspective, the important detail is the displayed wording, not the raw internal identifiers.

The webapp translates technical source values into stable labels such as:

- **Assets** and **Products** as primary inventory concepts
- friendly object family names like **Web Applications**, **API Services**, or **Knowledge Banks** in context
- friendly capability labels like **Data Engineering** or **Automation & Orchestration**
- friendly category labels like **Visual Recipes**, **Projects**, or **Generative AI & LLM**

This means the taxonomy documentation should be read as a description of the **Pulse UI language**, not as a low-level event-processing specification.

## Design Intent for v3

Compared with older Pulse material, the v3 taxonomy is meant to be clearer, more product-oriented, and more aligned with how users navigate the dashboard.

The v3 vision emphasizes:

- a clean separation between **inventory objects** and **activity domains**
- a clearer distinction between **Assets** and **Products**
- webapp-friendly naming that is understandable outside engineering teams
- a taxonomy that supports dashboard navigation, catalog pages, and high-level KPI storytelling

In short, Pulse v3 uses taxonomy to make the estate legible.

## Summary

Pulse v3 organizes the webapp around three complementary lenses:

- **Assets** show the core objects teams build with
- **Products** show the outputs teams deliver, publish, or operationalize
- **Capabilities** show the functional areas where development activity is grouped

That structure gives the Pulse dashboard a consistent vocabulary for exploring a Dataiku estate at both the inventory level and the activity level.
