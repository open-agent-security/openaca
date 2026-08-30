from __future__ import annotations

import json
from pathlib import Path

import httpx
from click.testing import CliRunner

from tools.agent_kinds import AgentInstance
from tools.cli import main as openaca_main
from tools.remote.client import BomUploadResult, DriftResult
from tools.remote.collector import EndpointCollection
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


def test_collect_endpoint_cli_passes_no_kind_or_config_dir_through_by_default(monkeypatch):
    """No --kind/--config-dir means every installed kind resolves its own
    default root (Task 9 Step 3) — `$CLAUDE_CONFIG_DIR`/`~/.claude` resolution
    now happens inside discovery, not eagerly in the CLI, so the CLI's job is
    just to pass `None`/`None` through unmodified."""
    calls: list[dict] = []

    def fake_collect_endpoint(**kwargs):
        calls.append(kwargs)
        return [_upload_result(asset_id="asset-123")]

    monkeypatch.setattr("tools.remote.cli.collect_endpoint", fake_collect_endpoint)

    result = CliRunner().invoke(openaca_main, ["remote", "sync", "endpoint"])

    assert result.exit_code == 0
    assert calls[0]["config_dir"] is None
    assert calls[0]["kind_id"] is None


def test_sync_endpoint_dry_run_defaults_to_claude_config_dir_env(tmp_path, monkeypatch):
    """End-to-end: with no --kind/--config-dir, `$CLAUDE_CONFIG_DIR` is still
    honored — resolution now happens inside `discover_agents`, not the CLI."""
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        '[remote]\napi_url = "http://remote.test"\ntoken = "ot_TEST"\nasset_id = "asset-123"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    # Isolate from the real ~/.cursor so this machine's default two-kind
    # discovery doesn't leak a second payload into the assertion below.
    monkeypatch.setattr("tools.agent_kinds.cursor.Path.home", lambda: tmp_path / "no-cursor-home")

    result = CliRunner().invoke(openaca_main, ["remote", "sync", "endpoint", "--dry-run"])

    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["bom"]["metadata"]["component"]["bom-ref"] == "root/claude-code"


def test_collect_endpoint_cli_forwards_external_scanners(tmp_path, monkeypatch):
    calls: list[dict] = []

    def fake_collect_endpoint(**kwargs):
        calls.append(kwargs)
        return [_upload_result(asset_id="asset-123")]

    monkeypatch.setattr("tools.remote.cli.collect_endpoint", fake_collect_endpoint)

    result = CliRunner().invoke(
        openaca_main,
        [
            "remote",
            "sync",
            "endpoint",
            "--kind",
            "claude-code",
            "--config-dir",
            str(tmp_path),
            "--scanner",
            "nvidia-skillspector",
        ],
    )

    assert result.exit_code == 0
    assert calls[0]["external_scanners"] == ("nvidia-skillspector",)


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
        last_seen_at="2026-05-27T12:00:00Z",
        created_at="2026-05-27T11:00:00Z",
        component_count=14,
    )


def test_sync_endpoint_config_dir_without_kind_is_a_hard_error(tmp_path):
    result = CliRunner().invoke(
        openaca_main, ["remote", "sync", "endpoint", "--config-dir", str(tmp_path)]
    )

    assert result.exit_code != 0
    assert "--config-dir requires --kind" in result.output


def test_sync_endpoint_unknown_kind_is_a_hard_error(tmp_path):
    result = CliRunner().invoke(
        openaca_main,
        [
            "remote",
            "sync",
            "endpoint",
            "--kind",
            "not-a-real-kind",
            "--config-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "unknown agent kind" in result.output.lower()


def test_sync_endpoint_dry_run_prints_the_payload_as_one_json_line(tmp_path, monkeypatch):
    """stdout stays machine-readable so the preview can be piped to jq."""
    config_path = tmp_path / "remote.toml"
    config_path.write_text(
        '[remote]\napi_url = "http://remote.test"\ntoken = "ot_TEST"\nasset_id = "asset-123"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.build_endpoint_collections", _fake_collection)

    def fail(**kwargs):
        raise AssertionError("dry run must not take the upload path")

    monkeypatch.setattr("tools.remote.cli.collect_endpoint", fail)

    result = CliRunner().invoke(
        openaca_main,
        [
            "remote",
            "sync",
            "endpoint",
            "--kind",
            "claude-code",
            "--config-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["asset_id"] == "asset-123"
    assert payload["bom"]["bomFormat"] == "CycloneDX"
    assert "Uploaded BOM" not in result.output


def test_sync_endpoint_dry_run_runs_without_remote_configuration(tmp_path, monkeypatch):
    """The upload path stops here with "Remote is not configured". A preview
    of what would be sent has nothing to configure, so it must not."""
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: tmp_path / "absent.toml")
    monkeypatch.setattr("tools.remote.collector.build_endpoint_collections", _fake_collection)

    result = CliRunner().invoke(
        openaca_main,
        [
            "remote",
            "sync",
            "endpoint",
            "--kind",
            "claude-code",
            "--config-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "not configured" not in result.output
    assert json.loads(result.output.strip())["asset_id"] == "(unregistered)"


def _fake_collection(**kwargs) -> list[EndpointCollection]:
    return [
        EndpointCollection(
            agent=AgentInstance(
                kind_id="claude-code",
                display_name="Claude Code",
                source="installed",
                root_label="claude-code",
                coverage_baseline="complete",
                # Redaction (`_prepare_upload_payload`) reads each collection's
                # own agent.config_root now, not the CLI's outer --config-dir —
                # a real installed AgentInstance always carries one.
                config_root=kwargs.get("config_dir") or Path("/fake/.claude"),
            ),
            bom={"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []},
            posture_findings=[],
            observations=[],
            component_count=0,
        )
    ]


def _asset_result_with_agents():
    from tools.remote.client import AgentStatusResult, AssetStatusResult

    return AssetStatusResult(
        id="asset-123",
        asset_type="endpoint",
        external_id="demo-host",
        display_name="demo-mbp",
        owner_clerk_user_id=None,
        team_name=None,
        metadata={},
        last_seen_at="2026-05-27T12:00:00Z",
        created_at="2026-05-27T11:00:00Z",
        component_count=14,
        agents=(
            AgentStatusResult(
                agent_kind="claude-code",
                agent_id=None,
                composition_source="installed",
                composition_coverage="complete",
                latest_bom_id="bom-cc",
                last_seen_at="2026-05-27T12:00:00Z",
            ),
            AgentStatusResult(
                agent_kind="cursor",
                agent_id=None,
                composition_source="installed",
                composition_coverage="partial",
                latest_bom_id="bom-cur",
                last_seen_at="2026-05-27T12:00:00Z",
            ),
        ),
    )


def _run_status(monkeypatch, tmp_path, asset) -> str:
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
            return _me_result()

        def get_asset(self, asset_id: str):
            return asset

    monkeypatch.setattr("tools.remote.cli.RemoteClient", FakeClient)
    result = CliRunner().invoke(openaca_main, ["remote", "status"])
    assert result.exit_code == 0
    return result.output


def test_status_reports_a_bom_per_agent(monkeypatch, tmp_path):
    """`Latest BOM: none` right after a successful upload was the symptom:
    status read the asset-level id, which Fleet stopped populating when BOMs
    moved under agents."""
    output = _run_status(monkeypatch, tmp_path, _asset_result_with_agents())

    assert "claude-code: bom-cc (complete)" in output
    assert "cursor: bom-cur (partial)" in output
    assert "Latest BOM: none" not in output


def test_status_says_so_when_no_agent_has_synced(monkeypatch, tmp_path):
    """A registered-but-never-synced machine has no agents and no BOM."""
    output = _run_status(monkeypatch, tmp_path, _asset_result())

    assert "Latest BOM: none — no agent has synced yet" in output
