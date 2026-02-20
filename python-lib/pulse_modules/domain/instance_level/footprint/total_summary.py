import pandas as pd

        
def main(self):
    df = self.dss_footprint
    if df is None or df.empty:
        return pd.DataFrame()
    
    # Total DSS Summary
    totals_cols = ["size", "nbFiles", "nbFolders", "nbErrors"]
    totals_df = (
        df[totals_cols]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    if len(totals_df) != 1:
        raise ValueError("Unexpected multiple totals rows detected.")
    return totals_df