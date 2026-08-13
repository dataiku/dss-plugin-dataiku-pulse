from __future__ import annotations


def gold_destination_for_table(table_name: str) -> str:
    if table_name.startswith("fact_"):
        return f"gold/{table_name}"
    if table_name.startswith(("base_", "dim_", "agg_")):
        return f"gold/{table_name}.parquet"
    raise ValueError(f"Unsupported GOLD table prefix for destination mapping: {table_name}")



def gold_destination_path(gold_ctx, relative_path: str) -> str:
    root = gold_ctx.folder_root.strip("/")
    rel = str(relative_path or "").lstrip("/")
    if root:
        rel = f"{root}/{rel}" if rel else root
    return f"{gold_ctx.blob_header}://{gold_ctx.bucket_or_container}/{rel}"
