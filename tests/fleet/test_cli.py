from __future__ import annotations

from click.testing import CliRunner

from tools.cli import main as openaca_main
from tools.fleet.client import BomUploadResult, DriftResult
from tools.fleet.config import load_fleet_config


def test_configure_writes_token_and_default_api_url(tmp_path, monkeypatch):
    config_path = tmp_path / "fleet.toml"
    monkeypatch.setattr("tools.fleet.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(openaca_main, ["fleet", "configure", "--token", "ot_TEST"])

    assert result.exit_code == 0
    assert "ot_TEST" not in result.output
    assert "ot_..." in result.output
    config = load_fleet_config(config_path)
    assert config.token == "ot_TEST"
    assert config.api_url == "https://api.openaca.dev"


def test_configure_accepts_api_url_override(tmp_path, monkeypatch):
    config_path = tmp_path / "fleet.toml"
    monkeypatch.setattr("tools.fleet.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(
        openaca_main,
        [
            "fleet",
            "configure",
            "--token",
            "ot_TEST",
            "--api-url",
            "http://localhost:8000",
        ],
    )

    assert result.exit_code == 0
    assert load_fleet_config(config_path).api_url == "http://localhost:8000"


def test_configure_prompts_for_token(tmp_path, monkeypatch):
    config_path = tmp_path / "fleet.toml"
    monkeypatch.setattr("tools.fleet.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(openaca_main, ["fleet", "configure"], input="ot_PROMPT\n")

    assert result.exit_code == 0
    assert "ot_PROMPT" not in result.output
    assert load_fleet_config(config_path).token == "ot_PROMPT"


def test_status_calls_me_and_configured_asset(tmp_path, monkeypatch):
    config_path = tmp_path / "fleet.toml"
    config_path.write_text(
        "\n".join(
            [
                "[fleet]",
                'api_url = "http://fleet.test"',
                'token = "ot_TEST"',
                'asset_id = "asset-123"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.fleet.cli.get_config_path", lambda: config_path)
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            calls.append(("init", {"api_url": api_url, "token": token}))

        def get_me(self):
            calls.append(("get_me", None))
            return _me_result()

        def get_asset(self, asset_id: str):
            calls.append(("get_asset", asset_id))
            return _asset_result()

    monkeypatch.setattr("tools.fleet.cli.FleetClient", FakeClient)

    result = CliRunner().invoke(openaca_main, ["fleet", "status"])

    assert result.exit_code == 0
    assert calls == [
        ("init", {"api_url": "http://fleet.test", "token": "ot_TEST"}),
        ("get_me", None),
        ("get_asset", "asset-123"),
    ]
    assert "Acme Inc" in result.output
    assert "demo-mbp" in result.output
    assert "14 components" in result.output


def test_status_without_asset_id_verifies_token_and_prints_next_step(tmp_path, monkeypatch):
    config_path = tmp_path / "fleet.toml"
    config_path.write_text(
        "\n".join(
            [
                "[fleet]",
                'api_url = "http://fleet.test"',
                'token = "ot_TEST"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.fleet.cli.get_config_path", lambda: config_path)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            calls.append("init")

        def get_me(self):
            calls.append("get_me")
            return _me_result()

        def get_asset(self, asset_id: str):
            raise AssertionError(f"unexpected asset lookup: {asset_id}")

    monkeypatch.setattr("tools.fleet.cli.FleetClient", FakeClient)

    result = CliRunner().invoke(openaca_main, ["fleet", "status"])

    assert result.exit_code == 0
    assert calls == ["init", "get_me"]
    assert "Acme Inc" in result.output
    assert "No asset configured" in result.output
    assert "openaca fleet collect endpoint" in result.output


def test_collect_endpoint_cli_honors_claude_config_dir_env(tmp_path, monkeypatch):
    calls: list[dict] = []

    def fake_collect_endpoint(**kwargs):
        calls.append(kwargs)
        return _upload_result(asset_id="asset-123")

    monkeypatch.setattr("tools.fleet.cli.collect_endpoint", fake_collect_endpoint)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    result = CliRunner().invoke(openaca_main, ["fleet", "collect", "endpoint"])

    assert result.exit_code == 0
    assert calls[0]["config_dir"] == tmp_path


def _upload_result(*, asset_id: str) -> BomUploadResult:
    return BomUploadResult(
        bom_id="bom-123",
        asset_id=asset_id,
        component_count=0,
        finding_count=0,
        policy_violation_count=0,
        drift=DriftResult(added=0, removed=0, changed=0),
        dashboard_url="https://app/boms/bom-123",
    )


def _me_result():
    from tools.fleet.client import MeResult, OrgResult, TokenResult

    return MeResult(
        org=OrgResult(id="org_123", name="Acme Inc"),
        token=TokenResult(id="tok_123", name="demo-token", last_used_at="2026-05-27T12:00:00Z"),
    )


def _asset_result():
    from tools.fleet.client import AssetStatusResult

    return AssetStatusResult(
        id="asset-123",
        asset_type="endpoint",
        external_id="demo-host",
        display_name="demo-mbp",
        owner_clerk_user_id=None,
        team_name=None,
        metadata={},
        latest_bom_id="bom-123",
        last_seen_at="2026-05-27T12:00:00Z",
        created_at="2026-05-27T11:00:00Z",
        component_count=14,
    )
