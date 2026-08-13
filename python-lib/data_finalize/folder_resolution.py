from dataiku.customrecipe import get_output_names_for_role


def resolve_gold_folder_lookup() -> str:
    outputs = get_output_names_for_role("gold_tables_folder")
    if not outputs:
        raise ValueError("Recipe requires one output managed folder for role 'gold_tables_folder'")
    return outputs[0]
