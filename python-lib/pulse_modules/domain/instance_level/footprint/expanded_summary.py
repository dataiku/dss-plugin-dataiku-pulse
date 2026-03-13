import pandas as pd

        
def main(self):
    df = self.dss_footprint
    if df is None or df.empty:
        return pd.DataFrame()
    
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
        .reset_index(names="object")
    )
    return summary_df