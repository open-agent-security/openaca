from __future__ import annotations

import httpx
from click.testing import CliRunner

from tools.cli import main as openaca_main
from tools.remote.client import BomUploadResult, DriftResult
from tools.remote.config import load_remote_config


def test_remote_is_public_upload_command_group() -> None:
    result = CliRunner().invoke(openaca_main, ["remote", "--help"])

    assert result.exit_code == 0
    assert "Configure opt-in remote uploads" in result.output


def test_fleet_command_group_is_not_public() -> None:
    result = CliRunner().invoke(openaca_main, ["fleet", "--help"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_configure_writes_token_and_default_api_url(tmp_path, monkeypatch):
    config_path = tmp_path / "remote.toml"
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(openaca_main, ["remote", "configure", "--token", "ot_TEST"])

    assert result.exit_code == 0
    assert "ot_TEST" not in result.output
    assert "ot_..." in result.output
    config = load_remote_config(config_path)
    assert config.token == "ot_TEST"
    assert config.api_url == "https://api.openaca.dev"


def test_configure_masked_token_shows_last4_for_disambiguation(tmp_path, monkeypatch):
    """A realistic-length token is displayed as prefix + last 4
    (`ot_...WXYZ`) so users with several tokens can tell which one is
    configured — matching the last-4 display the backend stores for the
    console (token_suffix). The rest of the secret must never appear.
    """
    config_path = tmp_path / "remote.toml"
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)
    token = "ot_A1b2C3d4E5f6G7h8WXYZ"

    result = CliRunner().invoke(openaca_main, ["remote", "configure", "--token", token])

    assert result.exit_code == 0
    assert "ot_...WXYZ" in result.output
    assert token not in result.output
    assert "A1b2C3d4E5f6G7h8" not in result.output


def test_mask_token_short_and_unknown_shapes_reveal_nothing():
    """A short token's last 4 could be most of its secret, and an
    unknown-shaped secret has no safe prefix — both stay fully masked.
    Real tokens are ot_ + 20+ chars; anything shorter stays fully masked
    so 4 chars never represent a meaningful fraction of the secret.
    """
    from tools.remote.cli import _mask_token

    assert _mask_token("ot_TEST") == "ot_..."
    assert _mask_token("something-else") == "***"
    # Mid-length ot_ token (9 secret chars) — suffix would be 44% of secret
    assert _mask_token("ot_123456789") == "ot_..."
    # One char below the threshold (22 total = ot_ + 19 secret chars)
    assert _mask_token("ot_" + "A" * 19) == "ot_..."


def test_configure_accepts_api_url_override(tmp_path, monkeypatch):
    config_path = tmp_path / "remote.toml"
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(
        openaca_main,
        [
            "remote",
            "configure",
            "--token",
            "ot_TEST",
            "--api-url",
            "http://localhost:8000",
        ],
    )

    assert result.exit_code == 0
    assert load_remote_config(config_path).api_url == "http://localhost:8000"


def test_configure_prompts_for_token(tmp_path, monkeypatch):
    config_path = tmp_path / "remote.toml"
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(openaca_main, ["remote", "configure"], input="ot_PROMPT\n")

    assert result.exit_code == 0
    assert "ot_PROMPT" not in result.output
    assert load_remote_config(config_path).token == "ot_PROMPT"


def test_configure_preserves_asset_id_when_credentials_unchanged(tmp_path, monkeypatch):
    """Re-running configure with identical token and api_url must not drop the cached asset_id."""
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        "\n".join(
            [
                "[remote]",
                'api_url = "https://api.openaca.dev"',
                'token = "ot_SAME"',
                'asset_id = "asset-123"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(openaca_main, ["remote", "configure", "--token", "ot_SAME"])

    assert result.exit_code == 0
    assert load_remote_config(config_path).asset_id == "asset-123"


def test_configure_clears_asset_id_when_token_changes(tmp_path, monkeypatch):
    """Changing the token on reconfigure must clear the cached asset_id to prevent
    uploads to an asset registered under a different org/token."""
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        "\n".join(
            [
                "[remote]",
                'api_url = "https://api.openaca.dev"',
                'token = "ot_OLD"',
                'asset_id = "asset-123"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(openaca_main, ["remote", "configure", "--token", "ot_NEW"])

    assert result.exit_code == 0
    assert load_remote_config(config_path).asset_id is None


def test_configure_clears_asset_id_when_api_url_changes(tmp_path, monkeypatch):
    """Changing api_url on reconfigure must clear the cached asset_id because the
    asset belongs to the old backend and cannot be used with the new one."""
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        "\n".join(
            [
                "[remote]",
                'api_url = "https://api.openaca.dev"',
                'token = "ot_TEST"',
                'asset_id = "asset-123"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)

    result = CliRunner().invoke(
        openaca_main,
        ["remote", "configure", "--token", "ot_TEST", "--api-url", "http://localhost:8000"],
    )

    assert result.exit_code == 0
    assert load_remote_config(config_path).asset_id is None


def test_configure_purges_pending_files_when_credentials_change(tmp_path, monkeypatch):
    """When token changes on reconfigure, any pending offline-cache files (which embed
    the old asset_id) must be purged so they are never replayed against the new backend."""
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        "\n".join(
            [
                "[remote]",
                'api_url = "https://api.openaca.dev"',
                'token = "ot_OLD"',
                'asset_id = "asset-123"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    (pending_dir / "pending-bom-stale.json").write_text(
        '{"asset_id":"asset-123"}', encoding="utf-8"
    )
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)

    result = CliRunner().invoke(openaca_main, ["remote", "configure", "--token", "ot_NEW"])

    assert result.exit_code == 0
    assert not list(pending_dir.glob("pending-bom-*.json")), "stale pending files must be purged"


def test_configure_does_not_purge_pending_files_when_credentials_unchanged(tmp_path, monkeypatch):
    """Re-running configure with identical credentials must not discard pending files."""
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        "\n".join(
            [
                "[remote]",
                'api_url = "https://api.openaca.dev"',
                'token = "ot_SAME"',
                'asset_id = "asset-123"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    (pending_dir / "pending-bom-keep.json").write_text('{"asset_id":"asset-123"}', encoding="utf-8")
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)

    result = CliRunner().invoke(openaca_main, ["remote", "configure", "--token", "ot_SAME"])

    assert result.exit_code == 0
    assert (pending_dir / "pending-bom-keep.json").exists(), "pending file must be preserved"


def test_status_calls_me_and_configured_asset(tmp_path, monkeypatch):
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        "\n".join(
            [
                "[remote]",
                'api_url = "http://remote.test"',
                'token = "ot_TEST"',
                'asset_id = "asset-123"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)
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

    monkeypatch.setattr("tools.remote.cli.RemoteClient", FakeClient)

    result = CliRunner().invoke(openaca_main, ["remote", "status"])

    assert result.exit_code == 0
    assert calls == [
        ("init", {"api_url": "http://remote.test", "token": "ot_TEST"}),
        ("get_me", None),
        ("get_asset", "asset-123"),
    ]
    assert "Acme Inc" in result.output
    assert "demo-mbp" in result.output
    assert "14 components" in result.output


def test_status_without_asset_id_verifies_token_and_prints_next_step(tmp_path, monkeypatch):
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        "\n".join(
            [
                "[remote]",
                'api_url = "http://remote.test"',
                'token = "ot_TEST"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)
    calls: list[str] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            calls.append("init")

        def get_me(self):
            calls.append("get_me")
            return _me_result()

        def get_asset(self, asset_id: str):
            raise AssertionError(f"unexpected asset lookup: {asset_id}")

    monkeypatch.setattr("tools.remote.cli.RemoteClient", FakeClient)

    result = CliRunner().invoke(openaca_main, ["remote", "status"])

    assert result.exit_code == 0
    assert calls == ["init", "get_me"]
    assert "Acme Inc" in result.output
    assert "No asset configured" in result.output
    assert "openaca remote sync endpoint" in result.output


def test_status_reports_network_failure_without_traceback(tmp_path, monkeypatch):
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        "\n".join(
            [
                "[remote]",
                'api_url = "http://remote.test"',
                'token = "ot_TEST"',
                'asset_id = "asset-123"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.remote.cli.get_config_path", lambda: config_path)

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def get_me(self):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("tools.remote.cli.RemoteClient", FakeClient)

    result = CliRunner().invoke(openaca_main, ["remote", "status"])

    assert result.exit_code != 0
    assert "Remote API unreachable: connection refused" in result.output
    assert "Traceback" not in result.output


def test_collect_endpoint_cli_honors_claude_config_dir_env(tmp_path, monkeypatch):
    calls: list[dict] = []

    def fake_collect_endpoint(**kwargs):
        calls.append(kwargs)
        return _upload_result(asset_id="asset-123")

    monkeypatch.setattr("tools.remote.cli.collect_endpoint", fake_collect_endpoint)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    result = CliRunner().invoke(openaca_main, ["remote", "sync", "endpoint"])

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
    from tools.remote.client import MeResult, OrgResult, TokenResult

    return MeResult(
        org=OrgResult(id="org_123", name="Acme Inc"),
        token=TokenResult(id="tok_123", name="demo-token", last_used_at="2026-05-27T12:00:00Z"),
    )


def _asset_result():
    from tools.remote.client import AssetStatusResult

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
