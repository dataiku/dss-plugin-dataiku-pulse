from __future__ import annotations

import pandas as pd

from data_collection.audit_logs_modules.event_mapping import main

# A real, non-DROP_DELETE msgType from mapping.csv (maps to ADMINISTRATION).
REAL_MSGTYPE = "admin-build-container-base-images"

# A real DROP_DELETE msgType from mapping.csv.
DROP_DELETE_MSGTYPE = "accessible-objects-count"


def test_anchored_prefix_strip_does_not_mangle_unrelated_columns():
    df = pd.DataFrame(
        {
            "message_msgType": [REAL_MSGTYPE],
            "topic": ["t1"],
            "timestamp": [1700000000000],
            "foo_message_bar": ["unchanged"],
        }
    )

    result = main(df)

    # Only the leading `message_` prefix is stripped (anchored replace) -
    # a column merely containing "message_" elsewhere must survive intact.
    assert "foo_message_bar" in result.columns
    assert "foo_bar" not in result.columns
    assert result.loc[result.index[0], "foo_message_bar"] == "unchanged"

    assert "msgtype" in result.columns
    assert result.loc[result.index[0], "msgtype"] == REAL_MSGTYPE


def test_unmapped_msgtype_is_dropped_and_counted():
    df = pd.DataFrame(
        {
            "message_msgType": [REAL_MSGTYPE, "definitely-not-a-real-msgtype-xyz"],
            "topic": ["t1", "t2"],
            "timestamp": [1700000000000, 1700000001000],
        }
    )

    result = main(df)

    assert result.shape[0] == 1
    assert "definitely-not-a-real-msgtype-xyz" not in result["msgtype"].tolist()
    assert result.attrs["unmapped_msgtype_rows"] == 1


def test_drop_delete_rows_are_removed():
    df = pd.DataFrame(
        {
            "message_msgType": [DROP_DELETE_MSGTYPE, REAL_MSGTYPE],
            "topic": ["t1", "t2"],
            "timestamp": [1700000000000, 1700000001000],
        }
    )

    result = main(df)

    assert result.shape[0] == 1
    assert result.loc[result.index[0], "msgtype"] == REAL_MSGTYPE
