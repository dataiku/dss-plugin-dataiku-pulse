from __future__ import annotations

from types import SimpleNamespace

from shared_duckdb import storage_config


class _ConnectionHandle:
    def __init__(self, info: dict):
        self._info = info

    def get_info(self) -> dict:
        return self._info


def _ctx(info: dict) -> SimpleNamespace:
    return SimpleNamespace(
        connection_name="test-connection",
        connection_handle=_ConnectionHandle(info),
        cached_connection_info={},
    )


def _aws_info(*, credentials_mode: str, use_path_mode: bool) -> dict:
    params = {
        "credentialsMode": credentials_mode,
        "regionOrEndpoint": "us-west-2",
        "usePathMode": use_path_mode,
    }
    if credentials_mode == "KEYPAIR":
        params.update({"accessKey": "static-access-key", "secretKey": "static-secret-key"})
        return {"params": params}
    return {
        "params": params,
        "resolvedAWSCredential": {
            "accessKey": "resolved-access-key",
            "secretKey": "resolved-secret-key",
            "sessionToken": "resolved-session-token",
        },
    }


def test_aws_credentials_renders_path_url_style_for_path_mode_sts():
    sql = storage_config.aws_credentials(_ctx(_aws_info(credentials_mode="STS_ASSUME_ROLE", use_path_mode=True)))

    assert "URL_STYLE 'path'" in sql
    assert "SESSION_TOKEN 'resolved-session-token'" in sql
    assert "REGION 'us-west-2'" in sql


def test_aws_credentials_renders_vhost_url_style_for_non_path_mode_sts():
    sql = storage_config.aws_credentials(_ctx(_aws_info(credentials_mode="STS_ASSUME_ROLE", use_path_mode=False)))

    assert "URL_STYLE 'vhost'" in sql
    assert "SESSION_TOKEN 'resolved-session-token'" in sql
    assert "REGION 'us-west-2'" in sql


def test_aws_credentials_renders_path_url_style_for_path_mode_static_credentials():
    sql = storage_config.aws_credentials(_ctx(_aws_info(credentials_mode="KEYPAIR", use_path_mode=True)))

    assert "URL_STYLE 'path'" in sql
    assert "KEY_ID 'static-access-key'" in sql
    assert "SECRET 'static-secret-key'" in sql
    assert "SESSION_TOKEN" not in sql
