import pandas as pd


def save_df(df):
    # RAW 
    try:
        long_results = dss_funcs._persist_raw(self, mn_df, category, module_name, None, file_name, [])
        results.append([category, f"write/save - {module_name} -- raw", True, None])
    except Exception as e:
        results.append([category, f"write/save - {module_name} -- raw", False, e])
        continue
    # SILVER
    try:
        long_results = dss_funcs._process_quality_and_persist(self, mn_df, category, module_name, None, category, file_name, [])
        results.append([category, f"write/save - {module_name} -- silver", True, None])
    except Exception as e:
        results.append([category, f"write/save - {module_name} -- silver", False, e])
    return

        
def main(self):
    try:
        # call API and create DF
        data = client.get_data_directories_footprint().compute_all_dss_footprint()
        df = pd.DataFrame(data)
    except:
        return pd.DataFrame()
    

    
    
    
    
    
    
    
    return pd.DataFrame()