import pandas as pd
from dataikupulse.src import dss_funcs


def main(self):
    mapping = dss_funcs.get_dss_name_id_mapping(self)
    df = pd.DataFrame(
        [mapping], columns=["instance_name", "instance_name_base", "instance_id_base"]
    )
    return df