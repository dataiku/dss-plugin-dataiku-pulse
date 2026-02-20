import pandas as pd

        
def main(self):
    if self.dss_footprint.empty:
        return pd.DataFrame()
    df = self.dss_footprint
    
    # Total DSS Summary
    totals_cols = ["size", "nbFiles", "nbFolders", "nbErrors"]
    totals_df = (
        df[totals_cols]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    return totals_df