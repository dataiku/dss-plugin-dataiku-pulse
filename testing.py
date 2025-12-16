def normalize_column_type(df: pd.DataFrame, col: str, default_if_str="None", default_if_bool=False):
    if col not in df.columns:
        df[col] = default_if_str
        return df

    # Look at non-null values
    df[col] = df[col].apply(lambda x: str(x) if not pd.isna(x) else "None")
    non_null_vals = df[col].dropna()

    if non_null_vals.empty:
        df[col] = default_if_str
        return df

    # Count types
    type_counts = non_null_vals.map(type).value_counts()

    # Pick the most common type
    main_type = type_counts.index[0]
    
    if main_type is bool:
        def to_bool(x):
            if isinstance(x, str):
                if x.lower() == "true":
                    return True
                elif x.lower() == "false":
                    return False
            return x
        df[col] = df[col].map(to_bool).fillna(default_if_bool).astype(bool)
    else:  # everything else → string
        df[col] = df[col].fillna(default_if_str).astype(str)

    return df
    
    





    # Normalize columns
    for col in df.columns:
        non_null_vals = df[col].dropna()
        if non_null_vals.empty:
            df[col] = None
            continue
        type_counts = non_null_vals.map(type).value_counts()
        main_type = type_counts.index[0]
        if main_type is bool:
            df[col] = df[col].map(to_bool).fillna(default_if_bool).astype(bool)
        else:  # everything else → string
            df[col] = df[col].fillna(default_if_str).astype(str)