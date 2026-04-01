import yaml


def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def render_query(query_str, **kwargs):
    return query_str.format(**kwargs)
