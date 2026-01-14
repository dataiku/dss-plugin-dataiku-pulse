# Partition Data Migration

The following document will cover how to cleanly migrate the `partition_data` folder from the previous setup/layout to the new v2.5 method. Below are the reasons for the change:

1. New Partition Layout - Instance Names are now moved under the `category/module` path. This aligns with categories/modules being static items, and instance_name now being dynamic changes like DATE pathing.
1. New Partition Layers - All data has been dropped down 1 layer to accomodate for RAW/SILVER layers. Splits the data between just gathered data (RAW) and cleansed, normalized, structured data (SILVER).

## How to Migrate

Log into Primary Pulse Instance and perform the following steps:

1. Waffle:Plugins:Installed - Find `Dataiku Pulse` and update from git to the latest version.
    1. Confirm Pulse Dashboard and Worker Nodes Parameter sets are still correct.
1. Open Pulse Dashboard Project.
1. New Partitioned Data Folder.
    1. Left Click the partitioned_data folder and in the right panel rename the folder to `partitioned_data_old`.
    1. Under Macros, run the `Initialize Dashboard` macro to create the new folder and update all other settings.
    1. The flow will now have 2 folders, old and new.
1. If multiple pulse instances are setup, run `Initialize Workers` to update their configs as well. Any data collected will go to the new folder.
1. Migrate Data.
    1. A Jupyter Notebook has been created to help migrate the data. Either by copying the code in, or uploading the attached notebook ensure the code has been loaded with the following setup:
        1. Run Locally
        1. Use default Python
    1. In the main body of the code (not the top functions) are 2 variables that need adjusted. Replace the current ID values with the current folder values. IDs can be collected from the URL of the folder if opened.

        ```python
        old_pd = dataiku.Folder("5oBqsIcg") # Older Folder
        new_pd = dataiku.Folder("OWehJb35") # New Folder
        ```

    1. Run each cell and be patient. This will take some times depending on the number of files and threads available
        1. Data is read in
        1. Paths are adjusted
        1. Data is saved to the new folder
        1. DATA IS NOT DELETED FROM ORIGINAL -- Allows for reruns or double checking

## Rebuild / Build Silver

1. Next we wil want to setup a scenario to rebuild the Silver layer. This process takes a lot more time than the data migration so it is better to run in a scenario.
1. Create a new scenario named `Rebuild SILVER Layer`
    1. Under Steps select Macro, then select `Initialize Silver Layer`
    1. RUN
1. Review the output folder for any objects under `raw_error` folder, either accept the files not being migrated or a hand fix solution can be available for anything that failed from the past conversion

## Pulse Dashboard

1. Restart the dashboard for the new data values, test, enjoy