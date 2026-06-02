from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from tools.cli import main as openaca_main
from tools.component_ref import ComponentRef
from tools.fleet.client import (
    BomUploadResult,
    DriftResult,
    FleetAuthError,
    RegisterAssetResult,
)
from tools.fleet.collector import (
    CollectError,
    EndpointCollection,
    build_endpoint_collection,
    collect_endpoint,
)
from tools.fleet.config import load_fleet_config
from tools.posture.finding import PostureFinding, Standards


def test_build_endpoint_collection_uses_endpoint_bom_and_posture_engine(tmp_path, monkeypatch):
    ref = ComponentRef(
        ecosystem="npm",
        name="@example/mcp",
        version=None,
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        component_identity="mcp-server/example",
        extra={"component_type": "mcp_server", "install_source": "@example/mcp"},
    )
    calls: list[tuple[str, object]] = []

    def fake_parse_install(**kwargs):
        calls.append(("parse_install", kwargs))
        return [ref], []

    def fake_run_posture_rules(refs, manifests, settings_manifests):
        calls.append(("run_posture_rules", refs))
        assert manifests == [("mcp", {})]
        assert settings_manifests == [("settings", {})]
        return [_posture("openaca-posture-mutable-install-reference")]

    monkeypatch.setattr("tools.fleet.collector.parse_install", fake_parse_install)
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [("mcp", {})],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [("settings", {})],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", fake_run_posture_rules)

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    assert calls[0][0] == "parse_install"
    assert calls[1] == ("run_posture_rules", [ref])
    assert collection.bom["metadata"]["properties"][1] == {
        "name": "openaca:target_type",
        "value": "endpoint",
    }
    assert {"name": "openaca:target", "value": "endpoint:user-scope"} in collection.bom["metadata"][
        "properties"
    ]
    assert collection.posture_findings == [
        {
            "rule_id": "openaca-posture-mutable-install-reference",
            "rule_version": "1",
            "severity": "LOW",
            "scope": "component",
            "component_identity": "mcp-server/example",
            "summary": "Mutable install",
            "fix": "Pin the install reference.",
            "evidence": {"install_ref": "@example/mcp", "manifest_path": ".mcp.json"},
        }
    ]


def test_build_endpoint_collection_trims_binary_install_source_argv(tmp_path, monkeypatch):
    ref = ComponentRef(
        component_identity="mcp-stdio/binary:python",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "python server.py --tenant alice --profile prod",
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "python"


def test_build_endpoint_collection_trims_npx_install_source_argv(tmp_path, monkeypatch):
    ref = ComponentRef(
        component_identity="mcp-stdio/npx-unpinned:@example/mcp",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx @example/mcp --token abc",
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "npx @example/mcp"


def test_build_endpoint_collection_trims_uvx_install_source_argv(tmp_path, monkeypatch):
    ref = ComponentRef(
        component_identity="mcp-stdio/uvx-unpinned:mcp-server",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "uvx -y mcp-server --api-key secret",
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "uvx mcp-server"


def test_build_endpoint_collection_trims_pinned_npm_install_source_argv(tmp_path, monkeypatch):
    ref = ComponentRef(
        ecosystem="npm",
        name="@scope/pkg",
        version="1.2.3",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx @scope/pkg@1.2.3 --token abc",
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "npx @scope/pkg@1.2.3"


def test_build_endpoint_collection_trims_pinned_pypi_install_source_argv(tmp_path, monkeypatch):
    ref = ComponentRef(
        ecosystem="PyPI",
        name="mcp-server",
        version="1.2.3",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "uvx mcp-server==1.2.3 --api-key secret",
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "uvx mcp-server==1.2.3"


def test_build_endpoint_collection_trims_pinned_github_install_source_argv(tmp_path, monkeypatch):
    ref = ComponentRef(
        ecosystem="github",
        name="oraios/serena",
        version="0123456789abcdef0123456789abcdef01234567",
        source_manifest=".mcp.json",
        source_locator="mcpServers.serena",
        extra={
            "component_type": "mcp_server",
            "install_source": (
                "uvx --from "
                "git+https://github.com/oraios/serena.git@0123456789abcdef0123456789abcdef01234567 "
                "serena --token secret"
            ),
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == (
        "uvx git+https://github.com/oraios/serena@0123456789abcdef0123456789abcdef01234567"
    )


@pytest.mark.parametrize(
    "raw_source, expected",
    [
        (
            "uvx --from git+https://github.com/oraios/serena.git@main serena --token secret",
            "uvx git+https://github.com/oraios/serena@main",
        ),
        (
            "uvx --from=git+https://github.com/oraios/serena serena --token secret",
            "uvx git+https://github.com/oraios/serena",
        ),
    ],
)
def test_build_endpoint_collection_trims_unversioned_github_install_source_argv(
    raw_source, expected, tmp_path, monkeypatch
):
    ref = ComponentRef(
        ecosystem="github",
        name="oraios/serena",
        version=None,
        source_manifest=".mcp.json",
        source_locator="mcpServers.serena",
        extra={
            "component_type": "mcp_server",
            "install_source": raw_source,
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == expected


def test_build_endpoint_collection_trims_pinned_docker_install_source_argv(tmp_path, monkeypatch):
    ref = ComponentRef(
        ecosystem="docker",
        name="hashicorp/terraform-mcp-server",
        version="0.4.0",
        source_manifest=".mcp.json",
        source_locator="mcpServers.terraform",
        extra={
            "component_type": "mcp_server",
            "install_source": (
                "docker run -i --rm -e TFE_TOKEN=${TFE_TOKEN} hashicorp/terraform-mcp-server:0.4.0"
            ),
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "docker hashicorp/terraform-mcp-server:0.4.0"


def test_build_endpoint_collection_trims_docker_digest_install_source_uses_at_separator(
    tmp_path, monkeypatch
):
    digest = "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    ref = ComponentRef(
        ecosystem="docker",
        name="ghcr.io/github/github-mcp-server",
        version=digest,
        source_manifest=".mcp.json",
        source_locator="mcpServers.github",
        extra={
            "component_type": "mcp_server",
            "install_source": (f"docker run -i --rm ghcr.io/github/github-mcp-server@{digest}"),
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == (f"docker ghcr.io/github/github-mcp-server@{digest}")


def test_build_endpoint_collection_trims_local_mcp_install_source_argv(tmp_path, monkeypatch):
    ref = ComponentRef(
        component_identity="mcp-stdio/local:discord",
        source_manifest=".mcp.json",
        source_locator="mcpServers.discord",
        extra={
            "component_type": "mcp_server",
            "install_source": "bun run --cwd ${CLAUDE_PLUGIN_ROOT} --shell=bun start",
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "bun"


def test_build_endpoint_collection_trims_pinned_npm_install_source_with_flag_prefix(
    tmp_path, monkeypatch
):
    ref = ComponentRef(
        ecosystem="npm",
        name="@scope/pkg",
        version="1.2.3",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx -y @scope/pkg@1.2.3 --token abc",
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "npx @scope/pkg@1.2.3"


def test_build_endpoint_collection_trims_pinned_pypi_install_source_with_flag_prefix(
    tmp_path, monkeypatch
):
    ref = ComponentRef(
        ecosystem="PyPI",
        name="mcp-server",
        version="1.2.3",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "uvx --from mcp-server==1.2.3 cmd --api-key secret",
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )
    monkeypatch.setattr("tools.fleet.collector.run_posture_rules", lambda *args: [])

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "uvx mcp-server==1.2.3"


def test_collect_endpoint_registers_asset_uploads_bom_and_saves_asset_id(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id=None)
    pending_dir = tmp_path / "pending"
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr("tools.fleet.collector.socket.gethostname", lambda: "demo-host")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            calls.append(("init", {"api_url": api_url, "token": token}))

        def register_asset(self, payload):
            calls.append(("register_asset", payload))
            return RegisterAssetResult(
                asset_id="asset-123", dashboard_url="https://app/assets/asset-123"
            )

        def upload_bom(self, payload):
            calls.append(("upload_bom", payload))
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    result = collect_endpoint(config_dir=tmp_path, project=None)

    assert result.asset_id == "asset-123"
    assert [name for name, _ in calls] == ["init", "register_asset", "upload_bom"]
    assert calls[1][1]["asset_type"] == "endpoint"
    assert calls[1][1]["external_id"] == "demo-host"
    assert calls[2][1]["asset_id"] == "asset-123"
    assert calls[2][1]["content_hash"].startswith("sha256:")
    assert calls[2][1]["posture_findings"][0]["rule_id"] == "openaca-posture-insecure-transport"
    assert load_fleet_config(config_path).asset_id == "asset-123"


def test_collect_endpoint_uses_existing_asset_id(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    calls: list[str] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            calls.append("init")

        def register_asset(self, payload):
            raise AssertionError("asset should not be re-registered")

        def upload_bom(self, payload):
            calls.append(payload["asset_id"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert calls == ["init", "asset-existing"]


def test_collect_endpoint_caches_payload_on_interactive_offline_failure(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert exc.value.exit_code == 2
    pending = list(pending_dir.glob("pending-bom-*.json"))
    assert len(pending) == 1
    cached = json.loads(pending[0].read_text(encoding="utf-8"))
    assert cached["asset_id"] == "asset-existing"


def test_collect_endpoint_converts_upload_client_error_to_collect_error(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise FleetAuthError("invalid or revoked token")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert exc.value.exit_code == 1
    assert str(exc.value) == "invalid or revoked token"


def test_collect_endpoint_converts_registration_network_error_to_collect_error(
    tmp_path, monkeypatch
):
    config_path = _write_config(tmp_path, asset_id=None)
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def register_asset(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert exc.value.exit_code == 2
    assert "asset registration failed" in str(exc.value)


def test_collect_endpoint_uploads_endpoint_inventory_paths(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    uploads: list[dict[str, Any]] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(
            bom={
                "bomFormat": "CycloneDX",
                "specVersion": "1.7",
                "components": [
                    {
                        "name": "mcp-server/test",
                        "properties": [
                            {
                                "name": "openaca:source_manifest",
                                "value": "/Users/alex/.claude/settings.json",
                            }
                        ],
                    }
                ],
            }
        ),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    props = uploads[0]["bom"]["components"][0]["properties"]
    assert props[0]["value"] == "/Users/alex/.claude/settings.json"


def test_write_pending_payload_creates_file_mode_0600(tmp_path, monkeypatch):
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError):
        collect_endpoint(config_dir=tmp_path, project=None)

    pending = list(pending_dir.glob("pending-bom-*.json"))
    assert len(pending) == 1
    assert stat.S_IMODE(os.stat(pending[0]).st_mode) == 0o600


def test_collect_endpoint_quiet_offline_failure_exits_zero_after_cache(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None, quiet=True)

    assert exc.value.exit_code == 0


def test_collect_endpoint_replays_pending_cache_before_current_upload(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    old_payload = _payload(asset_id="asset-existing", content_hash="sha256:old")
    (pending_dir / "pending-bom-1.json").write_text(json.dumps(old_payload), encoding="utf-8")
    uploads: list[str] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload["content_hash"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert uploads[0] == "sha256:old"
    assert len(uploads) == 2
    assert not list(pending_dir.glob("pending-bom-*.json"))


def test_collect_endpoint_continues_current_collection_when_replay_fails(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    old_payload = _payload(content_hash="sha256:old")
    (pending_dir / "pending-bom-1.json").write_text(json.dumps(old_payload), encoding="utf-8")

    collection_built: list[bool] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: (collection_built.append(True), _collection())[1],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None, allow_offline_cache=True)

    assert exc.value.exit_code == 0
    assert collection_built, "current endpoint collection must run even when replay fails"
    assert (pending_dir / "pending-bom-1.json").exists(), "old pending file kept for next attempt"
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 2, "new pending file written"


def test_collect_endpoint_skips_and_removes_corrupt_pending_file(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    (pending_dir / "pending-bom-bad.json").write_text("not-json!!!", encoding="utf-8")

    uploads: list[str] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload["content_hash"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert not (pending_dir / "pending-bom-bad.json").exists(), "corrupt file removed"
    assert len(uploads) == 1, "only the current upload ran, not the corrupt pending one"


def test_collect_endpoint_skips_replay_when_no_asset_id_registered(tmp_path, monkeypatch):
    """When asset_id is None (first run or post-reconfigure), replay must not run even
    if pending files are present — those files belong to a previous backend context."""
    config_path = _write_config(tmp_path, asset_id=None)
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    stale_payload = _payload(asset_id="old-asset-id", content_hash="sha256:stale")
    (pending_dir / "pending-bom-stale.json").write_text(json.dumps(stale_payload), encoding="utf-8")

    uploads: list[dict] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def register_asset(self, payload):
            from tools.fleet.client import RegisterAssetResult

            return RegisterAssetResult(
                asset_id="new-asset-id", dashboard_url="https://app/assets/new-asset-id"
            )

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 1, "only the current upload ran, not the stale pending one"
    assert uploads[0]["asset_id"] == "new-asset-id"
    assert (pending_dir / "pending-bom-stale.json").exists(), "stale file untouched by this run"


def test_collect_endpoint_purges_stale_asset_pending_files_on_replay(tmp_path, monkeypatch):
    """Pending files whose asset_id doesn't match the current config are purged on replay.

    Scenario: after a reconfiguration that reset asset_id to None, a new asset is registered.
    On the very next run the config has the new asset_id, but old pending files (written before
    reconfiguration) carry the old asset_id and must not be uploaded.
    """
    config_path = _write_config(tmp_path, asset_id="new-asset-id")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    stale_payload = _payload(asset_id="old-asset-id", content_hash="sha256:stale")
    stale_file = pending_dir / "pending-bom-stale.json"
    stale_file.write_text(json.dumps(stale_payload), encoding="utf-8")

    uploads: list[dict] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 1, "only the current upload ran, not the stale pending one"
    assert uploads[0]["asset_id"] == "new-asset-id"
    assert not stale_file.exists(), "stale pending file purged because asset_id mismatched"


def test_collect_endpoint_cli_prints_upload_summary(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_collect_endpoint(**kwargs):
        calls.append(kwargs)
        return _upload_result(asset_id="asset-123")

    monkeypatch.setattr("tools.fleet.cli.collect_endpoint", fake_collect_endpoint)

    result = CliRunner().invoke(
        openaca_main,
        ["fleet", "collect", "endpoint", "--config-dir", str(tmp_path), "--quiet"],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "config_dir": tmp_path,
            "project": None,
            "quiet": True,
            "allow_offline_cache": False,
        }
    ]
    assert "bom-123" in result.output
    assert "https://app/boms/bom-123" in result.output


def test_upload_cli_is_not_a_v0_command(tmp_path):
    bom_path = tmp_path / "bom.json"
    bom_path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["fleet", "upload", str(bom_path)])

    assert result.exit_code != 0
    assert "No such command" in result.output


def _write_config(tmp_path: Path, *, asset_id: str | None) -> Path:
    config_path = tmp_path / "fleet.toml"
    lines = [
        "[fleet]",
        'api_url = "http://fleet.test"',
        'token = "ot_TEST"',
    ]
    if asset_id is not None:
        lines.append(f'asset_id = "{asset_id}"')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def _collection(*, bom: dict[str, Any] | None = None) -> EndpointCollection:
    return EndpointCollection(
        bom=bom or {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []},
        posture_findings=[
            {
                "rule_id": "openaca-posture-insecure-transport",
                "rule_version": "1",
                "severity": "MEDIUM",
                "scope": "component",
                "component_identity": "mcp-server/test",
                "summary": "Insecure transport",
                "fix": "Use https.",
                "evidence": {"transport": "http", "manifest_path": ".mcp.json"},
            }
        ],
        component_count=0,
    )


def _payload(**overrides) -> dict[str, Any]:
    payload = {
        "asset_id": "asset-existing",
        "source": "endpoint",
        "openaca_version": "0.1.0b5",
        "target_locator": "endpoint:user-scope",
        "content_hash": "sha256:abc",
        "bom": {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []},
        "posture_findings": [],
    }
    payload.update(overrides)
    return payload


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


def _posture(rule_id: str) -> PostureFinding:
    return PostureFinding(
        rule_id=rule_id,
        title="Mutable install",
        severity="low",
        confidence="high",
        component={"type": "mcp_server", "name": "mcp-server/example (@example/mcp)"},
        active_in=["claude-code"],
        declared_by={"kind": "manifest", "path": ".mcp.json"},
        component_path=[{"type": "mcp_server", "name": "mcp-server/example"}],
        standards=Standards(),
        remediation="Pin the install reference.",
    )
