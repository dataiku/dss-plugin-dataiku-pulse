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
        cols = ["object"]
        if name in ["projects" , "orphanProjects"]:
            cols += ["projectKey"]
        elif name in ["codeEnvs", "plugins"]:
            cols += ["name"]
        else:
            self.logger.error(f"Unknown DSS Footprint name: {name}")
        cols += totals_cols
        tmp_df = tmp_df[cols]
        tmp_df.columns = ["object", "name"] + totals_cols
        dfs.append(tmp_df)

    #####################################################
    # END
    return pd.concat(dfs, ignore_index=True)