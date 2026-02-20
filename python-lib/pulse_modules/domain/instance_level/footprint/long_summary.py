import pandas as pd

def main(self):
    try:
        data = self.local_client.get_data_directories_footprint().compute_all_dss_footprint()
        df = pd.DataFrame(data)
    except:
        return pd.DataFrame()
    
    # Quick totals
    totals_cols = ["size", "nbFiles", "nbFolders", "nbErrors"]
    totals_df = (
        df[totals_cols]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    if len(totals_df) != 1:
        return pd.DataFrame()
    return totals_df