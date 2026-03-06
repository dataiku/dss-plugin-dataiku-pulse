# Pulse Metrics & Feature Wishlist

## Overview

The following notes capture suggested enhancements and potential metrics for **Pulse**. These are based on customer conversations, internal experience, and common best practices observed in the SSDE program.

The goal is to improve Pulse's ability to communicate **platform value, adoption, and operational impact** to customers and stakeholders.

---

# Potential Enhancements to Pulse

## 1. Consumption Metrics

One area that may currently be missing is **consumption-level metrics**.

Examples include:

- Number of times a **Machine Learning model** is used
- Number of times a **dashboard** is accessed
- Number of **agent or prompt calls** executed

These types of metrics are frequently requested by customers. Based on the level of interest seen in the **SSDE program**, many organizations want visibility into how their assets are being consumed.

Tracking these metrics would allow Pulse to better represent **actual usage and business value** of deployed assets.

---

## 2. External Data Artifact Utilization

Pulse could potentially be expanded to capture **utilization data outside of Dataiku** for Dataiku-generated data artifacts.

Example scenario:

A dataset created in Dataiku is written to a **data lake or lakehouse**, and then consumed by other systems.

Possible integrations could include:

- Query logs from data warehouses
- Lakehouse access metrics
- Downstream analytics platform usage

This would help customers see the **full lifecycle utilization of their data products**, even after they leave the Dataiku environment.

Note:  
This may require alignment with leadership regarding scope, but it could significantly strengthen the **Dataiku value proposition** for enterprise customers.

---

## 3. Recipe-Level Drilldown

Within **Usage Overview → Development**, Pulse currently aggregates activity into recipe categories.

This is helpful, but several customers have asked for the ability to:

- Drill down into **specific recipes used**
- View **individual recipe usage metrics**

This request has come up in discussions with multiple major customers (including Apple).

Suggested enhancement:

- Maintain the current **category-level aggregation**
- Allow **optional drill-down to recipe-level metrics**

This would enable deeper analysis while preserving the current high-level views.

---

## 4. Users Page Error

While reviewing the **Users page**, an error was encountered.

(This should likely be investigated separately to ensure stability and correctness of the metrics displayed.)

---

# Recommended Metrics for Pulse

Below is a broader set of metrics commonly recommended to customers.

Formatting guide:

- **Bold** = Should strongly align with Pulse
- *Italic* = Could align with Pulse depending on scope

---

## Core Platform Metrics

- **Number of users**
- **Active users**
- **Feature adoption**

---

## Value & Impact Metrics

- **ROAI / ROI** — Integration with value tracking or governance tools
- **Number of data products**
- **Number of data artifacts by type**
- **Data product cycle time**

---

## Enablement & Adoption Metrics

- *Number of users trained*  
  Could integrate with:
  - Dataiku in-product training
  - Customer internal training programs

- *Net Promoter Score (NPS)*  
  Could integrate with survey systems.

---

## Data Product Lifecycle Metrics

- **Data product maturity**
- **Number of reusable datasets**
- **Metadata completeness**  
  (Especially relevant if the customer uses the **Dataiku Data Catalog**)

---

## Operational & Business Metrics

- *Business process adoption*  
  Could be measured through integrations with:
  - Data warehouse query logs
  - External systems consuming Dataiku outputs

- *Internal process or feature usage*

---

# Summary

While it may not be realistic for Pulse to include all of these metrics, the above list represents a **wishlist of capabilities** that could significantly improve Pulse’s ability to:

- Demonstrate platform value
- Measure adoption
- Track the lifecycle of data products
- Provide executives and platform teams with actionable insights

These enhancements would make Pulse a stronger tool for **both operational monitoring and strategic reporting**.