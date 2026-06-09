import os
import stat

import pytest

from tools.remote.config import (
    ConfigError,
    RemoteConfig,
    get_config_path,
    load_remote_config,
    save_remote_config,
)


def test_config_path_defaults_to_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert get_config_path() == tmp_path / ".config" / "openaca" / "remote.toml"


def test_config_round_trips_api_url_token_and_asset_id(tmp_path):
    path = tmp_path / "remote.toml"
    config = RemoteConfig(
        api_url="https://api.example.test",
        token="ot_TEST_TOKEN",
        asset_id="asset-123",
    )

    save_remote_config(config, path)
    loaded = load_remote_config(path)

    assert loaded == config


def test_save_config_writes_file_mode_0600(tmp_path):
    path = tmp_path / "remote.toml"

    save_remote_config(RemoteConfig(token="ot_TEST_TOKEN"), path)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_load_missing_config_returns_defaults(tmp_path):
    assert load_remote_config(tmp_path / "missing.toml") == RemoteConfig()


def test_load_config_error_does_not_leak_token(tmp_path):
    path = tmp_path / "remote.toml"
    path.write_text(
        '[remote]\ntoken = "ot_SECRET_TOKEN"\napi_url = [not valid]\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc:
        load_remote_config(path)

    assert "ot_SECRET_TOKEN" not in str(exc.value)
    assert "remote.toml" in str(exc.value)
