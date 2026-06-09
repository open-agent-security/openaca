import os
import stat

import pytest

from tools.fleet.config import (
    ConfigError,
    FleetConfig,
    get_config_path,
    load_fleet_config,
    save_fleet_config,
)


def test_config_path_defaults_to_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    assert get_config_path() == tmp_path / ".config" / "openaca" / "remote.toml"


def test_config_round_trips_api_url_token_and_asset_id(tmp_path):
    path = tmp_path / "remote.toml"
    config = FleetConfig(
        api_url="https://api.example.test",
        token="ot_TEST_TOKEN",
        asset_id="asset-123",
    )

    save_fleet_config(config, path)
    loaded = load_fleet_config(path)

    assert loaded == config


def test_save_config_writes_file_mode_0600(tmp_path):
    path = tmp_path / "remote.toml"

    save_fleet_config(FleetConfig(token="ot_TEST_TOKEN"), path)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_load_missing_config_returns_defaults(tmp_path):
    assert load_fleet_config(tmp_path / "missing.toml") == FleetConfig()


def test_load_config_error_does_not_leak_token(tmp_path):
    path = tmp_path / "remote.toml"
    path.write_text(
        '[remote]\ntoken = "ot_SECRET_TOKEN"\napi_url = [not valid]\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc:
        load_fleet_config(path)

    assert "ot_SECRET_TOKEN" not in str(exc.value)
    assert "remote.toml" in str(exc.value)


def test_load_migrates_legacy_fleet_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".config" / "openaca" / "fleet.toml"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        '[fleet]\ntoken = "ot_LEGACY"\nasset_id = "asset-old"\n',
        encoding="utf-8",
    )

    config = load_fleet_config()

    assert config.token == "ot_LEGACY"
    assert config.asset_id == "asset-old"
    assert (tmp_path / ".config" / "openaca" / "remote.toml").exists()


def test_load_migration_skipped_when_remote_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_dir = tmp_path / ".config" / "openaca"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "fleet.toml").write_text('[fleet]\ntoken = "ot_OLD"\n', encoding="utf-8")
    (config_dir / "remote.toml").write_text('[remote]\ntoken = "ot_NEW"\n', encoding="utf-8")

    config = load_fleet_config()

    assert config.token == "ot_NEW"


def test_load_migration_ignores_corrupt_fleet_toml(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy = tmp_path / ".config" / "openaca" / "fleet.toml"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("not valid toml [[[", encoding="utf-8")

    config = load_fleet_config()

    assert config == FleetConfig()
