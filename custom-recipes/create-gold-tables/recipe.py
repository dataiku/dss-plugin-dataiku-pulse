import dataiku
from dataiku.customrecipe import get_input_names_for_role
from dataiku.customrecipe import get_output_names_for_role
from dataiku.customrecipe import get_recipe_config

#partitioned_data_folder = get_output_names_for_role('partitioned_data_folder')
#pdf = [dataiku.Dataset(name) for name in partitioned_data_folder]

my_variable = get_recipe_config().get('parameter_name', None)



from dataiku.customrecipe import get_plugin_config

# Returns the global settings of the plugin as a Python dictionary
plugin_config = get_plugin_config()


####################################################################################################################
# -*- coding: utf-8 -*-
import dataiku
import pandas as pd, numpy as np
from dataiku import pandasutils as pdu

print("MAZZEI hello")
print(plugin_config)