import pandas as pd

        
def main(self):
    results = []
    try:
        # call API and create DF
        data = client.get_data_directories_footprint().compute_all_dss_footprint()
        df = pd.DataFrame(data)
    except Exception as e:
        return [DSS Footprint, f"Loading API Module", False, e]
    
    #####################################################
    # Quick totals
    totals_cols = ["size", "nbFiles", "nbFolders", "nbErrors"]
    totals_df = (
        df[totals_cols]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    resultssave_df(self, df, category, module_name, file_name, results)
    
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