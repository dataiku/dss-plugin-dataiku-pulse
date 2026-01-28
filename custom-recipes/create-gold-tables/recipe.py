import dataiku
from dataiku.customrecipe import get_input_names_for_role
from dataiku.customrecipe import get_output_names_for_role
from dataiku.customrecipe import get_recipe_config

#output_A_names = get_output_names_for_role('main_output')
#output_A_datasets = [dataiku.Dataset(name) for name in output_A_names]

my_variable = get_recipe_config().get('parameter_name', None)


####################################################################################################################
# -*- coding: utf-8 -*-
import dataiku
import pandas as pd, numpy as np
from dataiku import pandasutils as pdu

print("MAZZEI hello")
print(my_variable)