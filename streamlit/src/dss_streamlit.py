import streamlit as st
import pandas as pd
from pandas.api.types import ( 
    is_categorical_dtype,
    is_datetime64_any_dtype,
    is_numeric_dtype,
    is_object_dtype,
    is_bool_dtype,
)
import importlib
import os
import re



# ---------- MODULES -----------------------------
def collect_modules(module):
    d = {}
    directory = module.__path__[0]
    for root, _, files in os.walk(directory):
        for f in files:
            if f.endswith(".py") and f != "__init__.py":
                module_name = f[:-3]
                path = root.replace(directory, "")
                fp = os.path.join(root, f)
                delimiters = r'[-_]'
                words = re.split(delimiters, module_name)
                capitalized_words = [word.capitalize() for word in words]
                final_string = " ".join(capitalized_words)
                d[final_string] = [module_name, fp]
    return d

    
def collect_display_data(load_modules):
    display_data = []
    modules = collect_modules(load_modules)
    for key in modules.keys():
        r_type = key.split(" ")
        r_type = r_type[0].lower()
        display_data.append(key)
    return modules, display_data


def load_insights(module_name, fp, filters = {}):
    results = {}
    spec = importlib.util.spec_from_file_location(module_name, fp)
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, 'main'):
            results = module.main(filters)
    except Exception as e:
        import streamlit as st
        st.error(f"Error importing or running ({fp}) {module_name}: {e}")
        results = {}
        return results
    return results


# ------------------------------------------------------------------------------------
def filter_dataframe(df):
    # Try to convert datetimes into a standard format (datetime, no timezone)
    for col in df.columns:
        if is_object_dtype(df[col]):
            try:
                df[col] = pd.to_datetime(df[col])
            except Exception:
                pass
        if is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.tz_localize(None)
    # Build container
    filter = df.columns
    to_filter_columns = st.multiselect("Filter dataframe rows on", filter)
    for column in to_filter_columns:
        left, right = st.columns((1, 20))
        left.write("↳")
        # Treat columns with < 10 unique values as categorical
        if is_categorical_dtype(df[column]) or df[column].nunique() < 10:
            user_cat_input = right.multiselect(
                f"Values for {column}",
                df[column].unique(),
                default=list(df[column].unique()),
            )
            df = df[df[column].isin(user_cat_input)]
        elif is_numeric_dtype(df[column]):
            _min = float(df[column].min())
            _max = float(df[column].max())
            step = (_max - _min) / 100
            user_num_input = right.slider(
                f"Values for {column}",
                _min,
                _max,
                (_min, _max),
                step=step,
            )
            df = df[df[column].between(*user_num_input)]
        elif is_datetime64_any_dtype(df[column]):
            user_date_input = right.date_input(
                f"Values for {column}",
                value=(
                    df[column].min(),
                    df[column].max(),
                ),
            )
            if len(user_date_input) == 2:
                user_date_input = tuple(map(pd.to_datetime, user_date_input))
                start_date, end_date = user_date_input
                df = df.loc[df[column].between(start_date, end_date)]
        else:
            user_text_input = right.text_input(
                f"Substring or regex in {column}",
            )
            if user_text_input:
                df = df[df[column].str.contains(user_text_input)]
    return df


def filter_base_data(path, filters):
    # read in df and if no filters return
    df = dss_folder.read_base_data(path)
    if not filters:
        return df
    # Append primary keys for filtering at different levels
    if "breaks on large data pulling into memory -- metadata.csv" in path:
        if "filter_df" not in st.session_state:
            st.session_state.filter_df = dss_folder.read_base_data("/metadata_primary_keys.csv")
        try:
            filter_df = st.session_state.filter_df
            on_key = filter_df.columns.tolist()
            for col in filter_df.columns.difference(df.columns):
                on_key.remove(col)
            df = pd.merge(df, filter_df, how="left", on=on_key)
            df.drop_duplicates(inplace=True)
        except:
            pass
    # Filter out the values based on the filter dictionary itself
    for column in filters.keys():
        values = filters[column]
        if column not in df.columns or not filters[column]:
            continue
        elif is_categorical_dtype(df[column]) or is_object_dtype(df[column]): # isinstance(series.dtype, pd.CategoricalDtype)
            df = df[df[column].isin(values)]
        elif is_bool_dtype(df[column]):
            if len(values) == 0 or len(values) == 2:
                df = df
            else:
                df = df[df[column] == values[0]]
        else:
            df = df[df[column].str.contains(values)]
    return df