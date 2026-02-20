import pandas as pd

        
def main(self):
    if self.dss_footprint.empty:
        return pd.DataFrame()
    df = self.dss_footprint.empty
    
    # Quick totals
    totals_cols = ["size", "nbFiles", "nbFolders", "nbErrors"]
    totals_df = (
        df[totals_cols]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    return totals_df

    #####################################################
    # Transpose (drop totals first)
    transpose_df = (
        df
        .drop(columns=totals_cols)
        .T
    )
    
    #####################################################
    # Summary table
    summary_df = (
        transpose_df
        .drop(columns=["items", "locations"], errors="ignore")
        .reset_index()
    )
    # Save output
    
    #####################################################
    # Object-level expansion
    for name, row in transpose_df.iterrows():
        if not isinstance(row.get("items"), list):
            continue
        tmp_df = pd.DataFrame(row["items"])
        tmp_df.insert(loc=0, column='object', value=name)
        # Save output

    #####################################################
    # END
    return results