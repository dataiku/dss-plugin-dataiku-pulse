import pandas as pd

def main(self):
    try:
        data = self.local_client.get_data_directories_footprint().compute_all_dss_footprint()
        df = pd.DataFrame(data)
    except:
        return pd.DataFrame()

    # Long Summary
    totals_cols = ["size", "nbFiles", "nbFolders", "nbErrors"]
    transpose_df = (
        df
        .drop(columns=totals_cols)
        .T
    )
    summary_df = (
        transpose_df
        .drop(columns=["items", "locations"], errors="ignore")
        .reset_index(names="object")
    )
    return summary_df