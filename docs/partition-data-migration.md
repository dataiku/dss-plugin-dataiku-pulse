# Partition Data Migration

This document describes how to cleanly migrate the `partitioned_data` folder from the previous layout to the new **v2.5 partitioning model**.

## Why This Change Was Made

1. **Updated Partition Layout**  
   Instance names have been moved under the `category/module` path.  
   This aligns with the fact that categories and modules are static, while `instance_name` behaves more like a dynamic dimension (similar to date-based pathing).

2. **Introduction of Partition Layers (RAW / SILVER)**  
   All data has been shifted down one directory level to support explicit `RAW` and `SILVER` layers.  
   This cleanly separates:
   - **RAW**: freshly collected, unmodified data  
   - **SILVER**: cleansed, normalized, and structured data  

## How to Migrate

Log into the **Primary Pulse Instance** and perform the following steps.

### 1. Update the Pulse Plugin

1. Navigate to **Waffle → Plugins → Installed**
2. Locate **Dataiku Pulse** and update it from Git to the latest version
3. Confirm that:
   - Pulse Dashboard parameters are still correct  
   - Worker node parameter sets are unchanged  

### 2. Prepare the Dashboard Project

1. Open the **Pulse Dashboard** project
2. Locate the existing `partitioned_data` folder
   1. Left-click the folder and rename it to `partitioned_data_old`
   2. Under **Macros**, run **Initialize Dashboard**
      - This creates the new folder structure
      - Updates all required settings
   3. The Flow should now show **two folders**: the old and the new

3. If multiple Pulse instances are configured:
   1. Run **Initialize Workers** to update their configurations
   2. All newly collected data will now write to the new folder

## Data Migration

1. A Jupyter Notebook is provided to assist with migrating existing data.
   - You may either copy the code into a notebook or upload the provided notebook file.

2. Ensure the notebook is configured as follows:
   - **Execution**: Run Locally  
   - **Kernel**: Default Python  

3. In the **main body of the notebook** (not the helper functions), update the following variables with the correct folder IDs:
   - Folder IDs can be obtained from the URL when opening the folder in Dataiku

   ```python
   old_pd = dataiku.Folder("5oBqsIcg")  # Old folder
   new_pd = dataiku.Folder("OWehJb35")  # New folder
   ```

4. Run each cell in sequence and allow the process to complete.
   - Execution time depends on file count and available threads

   During execution:
   - Data is read from the old folder
   - Paths are transformed to the new layout
   - Data is written to the new folder
   - **No data is deleted from the original folder**, allowing safe reruns and validation

## Rebuild / Build the SILVER Layer

1. After migration, the SILVER layer must be rebuilt.
   - This process can be time-consuming and is best run via a scenario.

2. Create a new scenario named **`Rebuild SILVER Layer`**
   1. Add a **Macro** step
   2. Select **Initialize Silver Layer**
   3. Run the scenario

3. After completion:
   - Review the output folders
   - Inspect any files under the `raw_error` directory
     - You may choose to accept missing historical files
     - Or manually correct and reprocess any failures

## Pulse Dashboard

1. Restart the Pulse Dashboard to load the new data
2. Validate results
3. Enjoy 🎉
