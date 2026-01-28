# Code for custom code recipe create-gold-tables (imported from a Python recipe)

# To finish creating your custom recipe from your original PySpark recipe, you need to:
#  - Declare the input and output roles in recipe.json
#  - Replace the dataset names by roles access in your code
#  - Declare, if any, the params of your custom recipe in recipe.json
#  - Replace the hardcoded params values by acccess to the configuration map

# See sample code below for how to do that.
# The code of your original recipe is included afterwards for convenience.
# Please also see the "recipe.json" file for more information.

# import the classes for accessing DSS objects from the recipe
import dataiku
# Import the helpers for custom recipes
from dataiku.customrecipe import get_input_names_for_role
from dataiku.customrecipe import get_output_names_for_role
from dataiku.customrecipe import get_recipe_config

#output_A_names = get_output_names_for_role('main_output')
#output_A_datasets = [dataiku.Dataset(name) for name in output_A_names]

# For optional parameters, you should provide a default value in case the parameter is not present:
my_variable = get_recipe_config().get('parameter_name', None)



####################################################################################################################
# -*- coding: utf-8 -*-
import dataiku
import pandas as pd, numpy as np
from dataiku import pandasutils as pdu

print("MAZZEI hello")
print(my_variable)