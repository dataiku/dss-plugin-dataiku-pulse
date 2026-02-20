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
    
    # Object-level expansion
    dfs = []
    for name, row in transpose_df.iterrows():
        if not isinstance(row.get("items"), list):
            continue
            
        tmp_df = pd.DataFrame(row["items"])
        tmp_df.insert(loc=0, column='object', value=name)
        
        # Determine name column source
        if name in ["projects", "orphanProjects"]:
            name_col = "projectKey"
        elif name in ["codeEnvs", "plugins"]:
            name_col = "name"
        else:
            self.logger.error(f"Unknown DSS Footprint name: {name}")
            continue
            
        # Ensure required columns exist
        required = ["object", name_col] + totals_cols
        missing = set(required) - set(tmp_df.columns)
        if missing:
            self.logger.error(f"Missing expected columns {missing} for {name}")
            continue
        tmp_df = tmp_df[required]

        # Standardize schema
        tmp_df.columns = ["object", "name"] + totals_cols
        dfs.append(tmp_df)

    #####################################################
    # END
    return pd.concat(dfs, ignore_index=True)