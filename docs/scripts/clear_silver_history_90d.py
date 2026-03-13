from datetime import datetime, timedelta
import io
import pandas as pd

import dataiku
from dataiku import pandasutils as pdu

client = dataiku.api_client()
project_handle = client.get_default_project()

folder = dataiku.Folder(
    lookup="partitioned_data",
    project_key=dataiku.default_project_key(),
    ignore_flow=True
)

# ------------------------------------------------------------------------------------
paths = folder.list_paths_in_partition()
df = pd.DataFrame(paths, columns=["paths"])
cols = ["dot", "layer", "category", "module", "instance_name", "year", "month", "day", "data"]
df[cols] = df["paths"].str.split("/", expand=True)
for c in cols:
    df[c] = df[c].str.replace(f"{c}=", "", regex=False)
df["year"] = df["year"].astype(int)
df["month"] = df["month"].astype(int)
df["day"] = df["day"].astype(int)
df["date"] = pd.to_datetime(df[["year", "month", "day"]])

# ------------------------------------------------------------------------------------
now = datetime.now()
ninety_days_ago = now - timedelta(days=90)
filtered_df = df.loc[
    (df["layer"] == "silver") &
    (df["date"] <= ninety_days_ago)
]

# ------------------------------------------------------------------------------------
total = len(filtered_df)
for idx, path in enumerate(filtered_df["paths"], start=1):
    folder.delete_path(path=path)
    if idx % 10 == 0 or idx == total:
        print(f"Progress: {idx}/{total} ({idx/total:.0%})")