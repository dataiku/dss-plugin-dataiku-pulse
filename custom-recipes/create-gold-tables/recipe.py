import dataiku
from dataiku.customrecipe import get_input_names_for_role
from dataiku.customrecipe import get_output_names_for_role
from dataiku.customrecipe import get_recipe_config

#partitioned_data_folder = get_output_names_for_role('partitioned_data_folder')
#pdf = [dataiku.Dataset(name) for name in partitioned_data_folder]

my_variable = get_recipe_config().get('parameter_name', None)



from dataiku.customrecipe import get_plugin_config
plugin_config = get_plugin_config()
"""
{
    'pulse_repo_url': 'https://github.com/dataiku/dss-plugin-dataiku-pulse.git',
    'pulse_repo_branch': 'mazzei-dev-2.1',
    'pulse_project_key': 'DATAIKU_PULSE',
    'pulse_worker_key': 'DATAIKU_PULSE_WORKER',
    'pulse_dataiku_user': 'smazzei',
    'ignore_certs': True,
    'do_parallel': True,
    'cores': 2,
    'api_configs': [
        {
            'worker_url': 'https://mazzei-designer.fe-aws.dkucloud-dev.com',
            'worker_api': 'dkuaps-MSleUwXxL2lCXEMQZVwiJNjMHfS0zIJ2'
        },
        {
            'worker_url': 'https://tam-global.fe-aws.dkucloud-dev.com',
            'worker_api': 'dkuaps-VcHO32hIj9tg8pFrMBnmirM340uE1rOC'
        }
    ],
    'pulse_project_url': 'https://mazzei-designer.fe-aws.dkucloud-dev.com',
    'pulse_folder_connection': 'mazzei-s3-bucket',
    'pulse_project_api': 'dkuaps-MSleUwXxL2lCXEMQZVwiJNjMHfS0zIJ2'
}
"""

####################################################################################################################
# -*- coding: utf-8 -*-
import dataiku
import pandas as pd, numpy as np
from dataiku import pandasutils as pdu

print("MAZZEI hello")
print(plugin_config)