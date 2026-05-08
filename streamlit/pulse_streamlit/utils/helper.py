from pulse_duckdb.engine import query

def get_instances():
    df = query.query_df("""
        SELECT DISTINCT instance_name
        FROM folder_partitions
        WHERE instance_name IS NOT NULL
        ORDER BY instance_name
    """)
    return df["instance_name"].tolist()


def get_canonical_capabilities():
    df = query.query_df("""
        SELECT DISTINCT canonical_capability
        FROM canonical_capabilities_base
        ORDER BY canonical_capability
    """)
    return df["canonical_capability"].tolist()


def get_subcategories_for_capability(canonical_capability):
    df = query.query_df(
        """
        SELECT DISTINCT dataiku_category
        FROM canonical_capabilities_base
        WHERE canonical_capability = '{canonical_capability}'
        ORDER BY dataiku_category
        """  # nosec
    )
    return df["dataiku_category"].tolist()


def format_capability_label(capability):
    label = capability.replace("_", " ").title()
    return (
        label
        .replace("Ml", "ML")
        .replace("Apis", "APIs")
        .replace("Genai", "GenAI")
        .replace("Llm", "LLM")
    )


def get_capability_summary_signal(capability: str, instance_name: str | None = None):
    where_instance = ""
    params = [capability]
    if instance_name:
        where_instance = "AND instance_name = ?"
        params.append(instance_name)

    df = query.query_df(
        (
            """
            SELECT
                dataiku_category,
                SUM(event_count) AS cnt
            FROM capability_subcategory_usage_last_30_days_base
            WHERE canonical_capability = ?
            {where_instance}
            GROUP BY dataiku_category
            ORDER BY cnt DESC
            LIMIT 1
            """
        ).format(where_instance=where_instance),  # nosec B608
        params=params,
    )
    if df.empty:
        return {}

    total_df = query.query_df(
        (
            """
            SELECT SUM(event_count) AS total
            FROM capability_subcategory_usage_last_30_days_base
            WHERE canonical_capability = ?
            {where_instance}
            """
        ).format(where_instance=where_instance),  # nosec B608
        params=params,
    )
    total = total_df["total"].iloc[0]

    top = df.iloc[0]
    return {
        "category": top["dataiku_category"],
        "share": top["cnt"] / total if total else 0
    }


def format_capability_summary(capability, signal):
    if not signal:
        return None

    cat = signal["category"]
    share = signal["share"]

    if capability == "ADVANCED_ANALYTICS_ML":
        if cat == "STATISTIC_ANALYTICS":
            return "ML activity is primarily focused on experimentation and analysis rather than operational deployment."
        if cat == "MLOPS":
            return "ML activity shows strong emphasis on model deployment and operationalization."

    if capability == "DATA_ENGINEERING":
        return f"Data Engineering work is primarily driven by {cat.lower().replace('_', ' ')} activities."

    if capability == "GENAI_LLM":
        return "GenAI usage is currently exploratory and concentrated on prompt-level interactions."

    return f"Usage within this capability is primarily driven by {cat.lower().replace('_', ' ')} activity."


def get_filtered_actors(filters):
    where_clauses = []
    params = []

    if filters["instance_name"] != "All (General)":
        where_clauses.append("instance_name = ?")
        params.append(filters["instance_name"])

    if filters["user_profiles"]:
        where_clauses.append("userProfile IN (SELECT * FROM UNNEST(?))")
        params.append(filters["user_profiles"])

    if filters["enabled_option"] == "Enabled only":
        where_clauses.append("enabled = TRUE")
    elif filters["enabled_option"] == "Disabled only":
        where_clauses.append("enabled = FALSE")

    where_clauses.append("last_activity_ts >= CURRENT_DATE - INTERVAL ? DAY")
    params.append(int(filters["days_since_activity"]))

    where_sql = " AND ".join(where_clauses)

    sql = f"""  # nosec
        SELECT
            DISTINCT
            instance_name,
            login,
            userProfile,
            enabled,
            last_activity_ts
        FROM users_metadata_base
        WHERE {where_sql}
        ORDER BY 
            instance_name,
            last_activity_ts DESC
    ;
    """

    return query.query_df(sql, params=params)
