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
        ecosystem="npm",
        name="@example/mcp",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx @example/mcp --token abc",
            "component_path": [{"type": "mcp_server", "name": "example"}],
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
        ecosystem="PyPI",
        name="mcp-server",
        source_manifest=".mcp.json",
        source_locator="mcpServers.example",
        extra={
            "component_type": "mcp_server",
            "install_source": "uvx -y mcp-server --api-key secret",
            "component_path": [{"type": "mcp_server", "name": "example"}],
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


def test_build_endpoint_collection_aligns_package_mcp_posture_to_graph_identity(
    tmp_path, monkeypatch
):
    ref = ComponentRef(
        ecosystem="npm",
        name="@playwright/mcp",
        source_manifest=".mcp.json",
        source_locator="$.mcpServers.playwright",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx @playwright/mcp@latest",
            "component_path": [{"type": "mcp_server", "name": "playwright"}],
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

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-server/playwright"
    assert collection.bom["components"][0]["bom-ref"] == "mcp-server/playwright"
    assert collection.posture_findings[0]["component_identity"] == "mcp-server/playwright"


def test_build_endpoint_collection_aligns_remote_mcp_posture_to_graph_identity(
    tmp_path, monkeypatch
):
    manifest_path = tmp_path / ".mcp.json"
    manifest = {"mcpServers": {"foo": {"url": "http://example.com/mcp"}}}
    ref = ComponentRef(
        component_identity="mcp-remote/example.com/mcp",
        source_manifest=str(manifest_path),
        source_locator="$.mcpServers.foo",
        extra={
            "component_type": "mcp_server",
            "transport": "http",
            "url": "http://example.com/mcp",
            "install_source": "http://example.com/mcp",
            "component_path": [{"type": "mcp_server", "name": "foo"}],
            "declared_by": {"kind": "manifest", "path": str(manifest_path)},
        },
    )

    monkeypatch.setattr("tools.fleet.collector.parse_install", lambda **kwargs: ([ref], []))
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_mcp_manifests",
        lambda config_dir, project, refs: [(manifest_path, manifest)],
    )
    monkeypatch.setattr(
        "tools.fleet.collector.collect_endpoint_settings_manifests",
        lambda config_dir, project: [],
    )

    collection = build_endpoint_collection(config_dir=tmp_path, project=None)

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-server/foo"
    assert collection.bom["components"][0]["bom-ref"] == "mcp-server/foo"
    assert collection.posture_findings[0]["component_identity"] == "mcp-server/foo"


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


def test_build_endpoint_collection_trims_github_subdirectory_install_source_argv(
    tmp_path, monkeypatch
):
    commit = "0123456789abcdef0123456789abcdef01234567"
    ref = ComponentRef(
        ecosystem="github",
        name="org/mono",
        version=commit,
        source_manifest=".mcp.json",
        source_locator="mcpServers.monorepo",
        extra={
            "component_type": "mcp_server",
            "source_subdirectory": "packages/mcp",
            "install_source": (
                "uvx --from "
                f"git+https://github.com/org/mono.git@{commit}#subdirectory=packages/mcp "
                "mcp --token secret"
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
        f"uvx git+https://github.com/org/mono@{commit}#subdirectory=packages/mcp"
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


def test_build_endpoint_collection_trims_binary_mcp_with_component_path(tmp_path, monkeypatch):
    # Realistic parser output: component_path is always set by _mcp_ref_extra, which causes
    # canonical_component_identity() to return mcp-server/<name> instead of mcp-stdio/binary:<cmd>.
    # Regression test for ADR-0029 identity: binary install_source must still be trimmed to 1 token.
    ref = ComponentRef(
        component_identity="mcp-stdio/binary:python",
        source_manifest=".mcp.json",
        source_locator="mcpServers.my-mcp",
        extra={
            "component_type": "mcp_server",
            "install_source": "python server.py --tenant alice --secret sk-1234",
            "component_path": [{"type": "mcp_server", "name": "my-mcp"}],
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
    assert props["openaca:identity"] == "mcp-server/my-mcp"
    assert props["openaca:install_source"] == "python"


def test_build_endpoint_collection_trims_local_mcp_with_component_path(tmp_path, monkeypatch):
    # Same as above for local (bun/php) MCPs whose identity is now mcp-server/<name>.
    ref = ComponentRef(
        component_identity="mcp-stdio/local:discord",
        source_manifest=".mcp.json",
        source_locator="mcpServers.discord",
        extra={
            "component_type": "mcp_server",
            "install_source": "bun run --cwd /home/user/plugin --shell=bun start",
            "component_path": [{"type": "mcp_server", "name": "discord"}],
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
    assert props["openaca:identity"] == "mcp-server/discord"
    assert props["openaca:install_source"] == "bun"


def test_build_endpoint_collection_trims_unpinned_npx_mcp_with_launcher_flags(
    tmp_path, monkeypatch
):
    # Realistic parser output: component_path causes canonical_component_identity() to return
    # mcp-server/<name>. _is_package_mcp_component must detect the ADR-0029 unpinned case
    # (first argv token is npx, no PURL) and extract the package, skipping flags like -y.
    # Regression test: before this fix, the component fell through to _trim_pinned_install_source
    # and the fallback kept two raw tokens ("npx -y") instead of "npx @scope/pkg".
    ref = ComponentRef(
        ecosystem="npm",
        name="@scope/pkg",
        source_manifest=".mcp.json",
        source_locator="mcpServers.my-mcp",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx -y @scope/pkg --token sk-1234",
            "component_path": [{"type": "mcp_server", "name": "my-mcp"}],
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
    assert props["openaca:identity"] == "mcp-server/my-mcp"
    assert props["openaca:install_source"] == "npx @scope/pkg"


def test_build_endpoint_collection_trims_unpinned_uvx_mcp_with_launcher_flags(
    tmp_path, monkeypatch
):
    # Same as above but for a uvx-launched unpinned MCP.
    ref = ComponentRef(
        ecosystem="PyPI",
        name="my-tool",
        source_manifest=".mcp.json",
        source_locator="mcpServers.my-tool",
        extra={
            "component_type": "mcp_server",
            "install_source": "uvx --python 3.11 my-tool --api-key secret",
            "component_path": [{"type": "mcp_server", "name": "my-tool"}],
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
    assert props["openaca:identity"] == "mcp-server/my-tool"
    # --python is a value-taking flag; "3.11" is its argument, not the package.
    # "my-tool" is the first positional after the flags.
    assert props["openaca:install_source"] == "uvx my-tool"


def test_build_endpoint_collection_trims_uvx_short_python_flag(tmp_path, monkeypatch):
    ref = ComponentRef(
        ecosystem="PyPI",
        name="my-tool",
        source_manifest=".mcp.json",
        source_locator="mcpServers.my-tool",
        extra={
            "component_type": "mcp_server",
            "install_source": "uvx -p 3.11 my-tool --api-key secret",
            "component_path": [{"type": "mcp_server", "name": "my-tool"}],
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
    assert props["openaca:identity"] == "mcp-server/my-tool"
    assert props["openaca:install_source"] == "uvx my-tool"


def test_build_endpoint_collection_trims_uv_tool_run_as_package_launch(tmp_path, monkeypatch):
    ref = ComponentRef(
        ecosystem="PyPI",
        name="weather-mcp",
        source_manifest=".mcp.json",
        source_locator="mcpServers.weather",
        extra={
            "component_type": "mcp_server",
            "install_source": "uv tool run weather-mcp --token secret",
            "component_path": [{"type": "mcp_server", "name": "weather"}],
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
    assert props["openaca:identity"] == "mcp-server/weather"
    assert "openaca:match_coordinate" not in props
    assert props["openaca:install_source"] == "uvx weather-mcp"


@pytest.mark.parametrize(
    "raw_source, expected",
    [
        (
            "npx --package @scope/pkg cmd --token sk-1234",
            "npx @scope/pkg",
        ),
        (
            "npx --package=@scope/pkg cmd --token sk-1234",
            "npx @scope/pkg",
        ),
        (
            "npx -p @scope/pkg cmd --token sk-1234",
            "npx @scope/pkg",
        ),
        # npx option-terminator form: `npx -- <pkg>` is documented as `npm exec -- <pkg>`
        (
            "npx -- @scope/pkg --token sk-1234",
            "npx @scope/pkg",
        ),
    ],
)
def test_build_endpoint_collection_trims_npx_package_flag_install_source(
    raw_source, expected, tmp_path, monkeypatch
):
    # npx --package <pkg> cmd [...] installs <pkg> then runs cmd. For Fleet inventory the
    # package is what matters; before this fix, the helper returned the command name instead.
    # Regression: component_path causes ADR-0029 identity so the argv-recovery path is taken.
    ref = ComponentRef(
        ecosystem="npm",
        name="@scope/pkg",
        source_manifest=".mcp.json",
        source_locator="mcpServers.my-mcp",
        extra={
            "component_type": "mcp_server",
            "install_source": raw_source,
            "component_path": [{"type": "mcp_server", "name": "my-mcp"}],
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
    assert props["openaca:identity"] == "mcp-server/my-mcp"
    assert props["openaca:install_source"] == expected


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


def test_collect_endpoint_uploads_content_hash_of_redacted_bom(tmp_path, monkeypatch):
    """Fleet's contract defines `content_hash = sha256(raw_bom)`. Before this
    fix, `_upload_payload` computed the hash, then `_redact_payload_for_fleet`
    mutated `payload["bom"]` in place, so the wire payload carried a hash
    of the pre-redacted BOM while the backend stored the post-redacted BOM
    under that hash. This test reproduces the upload path with a dirty BOM
    (absolute path under config_dir) and asserts the hash on the wire
    matches the BOM on the wire.
    """
    from tools.fleet.collector import _content_hash

    config_path = _write_config(tmp_path, asset_id="asset-existing")
    dirty_bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "components": [
            {
                "type": "application",
                "name": "clerk-cli",
                "properties": [
                    {
                        "name": "openaca:source_manifest",
                        "value": str(tmp_path / "skills" / "clerk-cli" / "SKILL.md"),
                    }
                ],
            }
        ],
    }
    captured: dict[str, Any] = {}

    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.fleet.collector.build_endpoint_collection",
        lambda config_dir, project: _collection(bom=dirty_bom),
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            captured["payload"] = payload
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.fleet.collector.FleetClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    payload = captured["payload"]
    # The absolute path must have been redacted out of the BOM.
    redacted_value = payload["bom"]["components"][0]["properties"][0]["value"]
    assert not redacted_value.startswith(str(tmp_path)), redacted_value
    # And the content_hash field must equal sha256 of the (post-redaction)
    # BOM actually being uploaded — not the hash of some prior BOM state.
    assert payload["content_hash"] == _content_hash(payload["bom"])


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


def test_collect_endpoint_redacts_absolute_paths_before_upload(tmp_path, monkeypatch):
    """ADR 0003: the CLI redacts absolute paths before upload so the Fleet
    backend's redaction check passes. Paths under config_dir are
    relativized; paths under an unknown root fall back to basename.
    """
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    uploads: list[dict[str, Any]] = []
    monkeypatch.setattr("tools.fleet.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.fleet.collector.get_pending_dir", lambda: tmp_path / "pending")

    # Two openaca:* properties: one inside the test's config_dir (tmp_path)
    # which should relativize, and one outside which should fall back to
    # basename.
    inside = tmp_path / "skills" / "x" / "SKILL.md"
    outside = "/Users/alex/.claude/settings.json"
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
                            {"name": "openaca:source_manifest", "value": str(inside)},
                            {"name": "openaca:source_manifest", "value": outside},
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
    # Inside config_dir → relativized; outside config_dir → basename fallback.
    assert props[0]["value"] == "skills/x/SKILL.md"
    assert props[1]["value"] == "settings.json"


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
        ["remote", "sync", "endpoint", "--config-dir", str(tmp_path), "--quiet"],
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

    result = CliRunner().invoke(openaca_main, ["remote", "upload", str(bom_path)])

    assert result.exit_code != 0
    assert "No such command" in result.output


def _write_config(tmp_path: Path, *, asset_id: str | None) -> Path:
    config_path = tmp_path / "remote.toml"
    lines = [
        "[remote]",
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
