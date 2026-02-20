# Silver Layer Column Catalog
<!-- TOC -->

- [Silver Layer Column Catalog](#silver-layer-column-catalog)
  - [agent](#agent)
    - [tools_metadata](#tools_metadata)
  - [agents](#agents)
    - [metadata](#metadata)
  - [audit](#audit)
    - [dataiku_usage](#dataiku_usage)
  - [datasets](#datasets)
    - [metadata](#metadata)
  - [footprint](#footprint)
    - [expanded_summary](#expanded_summary)
    - [objects_summary](#objects_summary)
    - [total_summary](#total_summary)
  - [instance](#instance)
    - [connections](#connections)
    - [instance_info](#instance_info)
    - [license](#license)
    - [name_mapping](#name_mapping)
  - [knowledge](#knowledge)
    - [banks_metadata](#banks_metadata)
  - [llms](#llms)
    - [metadata](#metadata)
  - [operating_system](#operating_system)
    - [diskspace](#diskspace)
    - [filesystem](#filesystem)
  - [projects](#projects)
    - [metadata](#metadata)
  - [recipes](#recipes)
    - [metadata](#metadata)
  - [scenarios](#scenarios)
    - [metadata](#metadata)
  - [users](#users)
    - [git_history](#git_history)
    - [metadata](#metadata)
    - [user_login_activity](#user_login_activity)

<!-- /TOC -->

- Files scanned: 21

## agent

### tools_metadata  

*File:* `agent_tools_metadata.py`  
*Columns:* 4

- `project_key`
- `agent_tools_id`
- `agent_tools_type`
- `agent_tools_name`

## agents

### metadata  

*File:* `agents_metadata.py`  
*Columns:* 12

- `project_key`
- `agents_id`
- `agents_name`
- `agents_type`
- `agents_activeVersion`
- `agents_versions_versionId`
- `agents_versions_versionTag_versionNumber`
- `agents_versions_versionTag_lastModifiedOn`
- `agents_versions_versionTag_lastModifiedBy_login`
- `agents_versions_creationTag_versionNumber`
- `agents_versions_creationTag_lastModifiedOn`
- `agents_versions_creationTag_lastModifiedBy_login`

## audit

### dataiku_usage  

*File:* `audit_dataiku_usage.py`  
*Columns:* 19

- `severity`
- `logger`
- `topic`
- `audittopic`
- `msgtype`
- `msgtypebase`
- `dataiku_category`
- `login`
- `authsource`
- `authvia`
- `user`
- `callpath`
- `clientip`
- `originalip`
- `xforwardedfor`
- `timestamp`
- `date`
- `instance_name`
- `project_key`

## datasets

### metadata  

*File:* `datasets_metadata.py`  
*Columns:* 13

- `project_key`
- `dataset_name`
- `dataset_smartName`
- `dataset_type`
- `dataset_managed`
- `dataset_featureGroup`
- `dataset_typeSystemVersion`
- `dataset_versionTag_versionNumber`
- `dataset_versionTag_lastModifiedOn`
- `dataset_versionTag_lastModifiedBy_login`
- `dataset_creationTag_versionNumber`
- `dataset_creationTag_lastModifiedOn`
- `dataset_creationTag_lastModifiedBy_login`

## footprint

### expanded_summary  

*File:* `footprint_expanded_summary.py`  
*Columns:* 5

- `object`
- `size`
- `nbFiles`
- `nbFolders`
- `nbErrors`

### objects_summary  

*File:* `footprint_objects_summary.py`  
*Columns:* 6

- `object`
- `name`
- `size`
- `nbFiles`
- `nbFolders`
- `nbErrors`

### total_summary  

*File:* `footprint_total_summary.py`  
*Columns:* 4

- `size`
- `nbFiles`
- `nbFolders`
- `nbErrors`

## instance

### connections  

*File:* `instance_connections.py`  
*Columns:* 5

- `name`
- `type`
- `connection_category`
- `creationTag_lastModifiedBy_login`
- `creationTag_lastModifiedOn`

### instance_info  

*File:* `instance_instance_info.py`  
*Columns:* 15

- `nodeId`
- `nodeName`
- `nodeType`
- `rawNodeType`
- `hostname`
- `installId`
- `dipInstanceId`
- `licenseInstanceId`
- `licenseId`
- `dssVersion`
- `os`
- `osVersion`
- `javaVendor`
- `javaVersion`
- `dssStartupTimestamp`

### license  

*File:* `instance_license.py`  
*Columns:* 2

- `profile`
- `licensed_limit`

### name_mapping  

*File:* `name_mapping.py`  
*Columns:* 4

- `instance_name`
- `instance_name_base`
- `instance_id_base`
- `timestamp`

## knowledge

### banks_metadata  

*File:* `knowledge_banks_metadata.py`  
*Columns:* 16

- `project_key`
- `knowledge_banks_id`
- `knowledge_banks_name`
- `knowledge_banks_retrieverType`
- `knowledge_banks_vectorStoreType`
- `knowledge_banks_embeddingLLMId`
- `knowledge_banks_rebuildBehavior`
- `knowledge_banks_envSelection_envMode`
- `knowledge_banks_envSelection_envName`
- `knowledge_banks_containerExecSelection_containerMode`
- `knowledge_banks_versionTag_versionNumber`
- `knowledge_banks_versionTag_lastModifiedOn`
- `knowledge_banks_versionTag_lastModifiedBy.login`
- `knowledge_banks_creationTag_versionNumber`
- `knowledge_banks_creationTag_lastModifiedOn`
- `knowledge_banks_creationTag_lastModifiedBy_login`

## llms

### metadata  

*File:* `llms_metadata.py`  
*Columns:* 6

- `llms_id`
- `llms_friendlyName`
- `llms_friendlyNameShort`
- `llms_type`
- `llms_connection`
- `llms_model`

## operating_system

### diskspace  

*File:* `operating_system_diskspace.py`  
*Columns:* 8

- `instance_name`
- `level_1`
- `level_2`
- `level_3`
- `level_1_size`
- `level_2_size`
- `level_3_size`
- `timestamp`

### filesystem  

*File:* `operating_system_filesystem.py`  
*Columns:* 8

- `instance_name`
- `filesystem`
- `size`
- `used`
- `available`
- `used_pct`
- `mounted_on`
- `timestamp`

## projects

### metadata  

*File:* `projects_metadata.py`  
*Columns:* 15

- `project_key`
- `project_name`
- `project_projectType`
- `project_projectAppType`
- `login`
- `project_ownerDisplayName`
- `project_permissionsVersion`
- `project_commitMode`
- `project_tutorialProject`
- `project_versionTag_versionNumber`
- `project_versionTag_lastModifiedOn`
- `project_versionTag_lastModifiedBy_login`
- `project_creationTag_versionNumber`
- `project_creationTag_lastModifiedOn`
- `project_creationTag_lastModifiedBy_login`

## recipes

### metadata  

*File:* `recipes_metadata.py`  
*Columns:* 16

- `project_key`
- `recipes_name`
- `recipes_type`
- `recipes_neverRecomputeExistingPartitions`
- `recipes_redispatchPartitioning`
- `recipes_maxRunningActivities`
- `recipes_hashPropagationBehavior`
- `recipes_params_engineType`
- `recipes_params_engineLabel`
- `recipes_params_engineRecommended`
- `recipes_versionTag_versionNumber`
- `recipes_versionTag_lastModifiedOn`
- `recipes_versionTag_lastModifiedBy_login`
- `recipes_creationTag_versionNumber`
- `recipes_creationTag_lastModifiedOn`
- `recipes_creationTag_lastModifiedBy_login`

## scenarios

### metadata  

*File:* `scenarios_metadata.py`  
*Columns:* 13

- `project_key`
- `scenarios_id`
- `scenarios_name`
- `scenarios_type`
- `scenarios_active`
- `scenarios_unavailable`
- `scenarios_running`
- `scenarios_markedAsTest`
- `scenarios_createdOn`
- `scenarios_lastModifiedOn`
- `scenarios_start`
- `scenarios_nextRun`
- `scenarios_runAsUser`

## users

### git_history  

*File:* `users_git_history.py`  
*Columns:* 5

- `project_key`
- `commit`
- `login`
- `timestamp`
- `message`

### metadata  

*File:* `users_metadata.py`  
*Columns:* 11

- `login`
- `displayName`
- `email`
- `userProfile`
- `resultingUserProfile`
- `enabled`
- `sourceType`
- `creationDate`
- `last_session_activity`
- `first_commit_date`
- `last_commit_date`

### user_login_activity  

*File:* `users_user_login_activity.py`  
*Columns:* 3

- `login`
- `activity_type`
- `timestamp`
