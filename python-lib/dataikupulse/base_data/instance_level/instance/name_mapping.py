import pandas as pd
from dataikupulse.src.dss_funcs import get_dss_name_id_mapping


def main(self, client, client_d = {}):
    mapping = get_dss_name_id_mapping(client)
    df = pd.DataFrame(
        [mapping], columns=["instance_name", "instance_name_base", "instance_id_base"]
    )
    return df