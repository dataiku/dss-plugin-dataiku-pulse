import pandas as pd

        
def main(self):
    if self.dss_footprint.empty:
        return pd.DataFrame()
    df = self.dss_footprint.empty
    
    # Transpose (drop totals first)
    totals_cols = ["size", "nbFiles", "nbFolders", "nbErrors"]
    transpose_df = (
        df
        .drop(columns=totals_cols)
        .T
    )
    
    # Summary table
    summary_df = (
        transpose_df
        .drop(columns=["items", "locations"], errors="ignore")
        .reset_index()
    )
    return summary_df