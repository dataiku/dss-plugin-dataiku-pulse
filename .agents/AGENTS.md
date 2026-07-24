# Local AGENTS.md

Local-only instructions for AI coding agents in this workspace.

## SILVER Catalog Reference

When investigating GOLD-table or web-application issues in this environment, use `/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/silver_catalog/` as the first reference point for SILVER validation.

The catalog contains:
- SILVER table listings
- Metadata
- Five sample rows per table

Before proposing or applying downstream fixes:
1. Confirm the SILVER source data is correct.
2. Confirm the SILVER metadata is correct.
3. Patch GOLD-table logic only if the issue exists after SILVER validation.
4. Patch web application views or other downstream tables only if the issue still remains after GOLD verification.

Do not assume a downstream patch is needed before confirming the source is correct.
