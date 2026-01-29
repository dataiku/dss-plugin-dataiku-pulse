import yaml

def load_yaml(path):
    try:
        with open(path, "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        raise e
    return config

def render_query(query_str, **kwargs):
    return query_str.format(**kwargs)