from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from tools.agent_kinds import AgentInstance
from tools.cli import main as openaca_main
from tools.component_ref import ComponentRef
from tools.observations.finding import ObservationFinding
from tools.observations.skillspector import SkillSpectorFindings
from tools.posture.finding import PostureFinding, Standards
from tools.remote.client import (
    BomUploadResult,
    DriftResult,
    RegisterAssetResult,
    RemoteAuthError,
    RemoteValidationError,
)
from tools.remote.collector import (
    DRY_RUN_UNREGISTERED_ASSET_ID,
    CollectError,
    EndpointCollection,
    build_endpoint_collections,
    build_endpoint_dry_run_payloads,
    collect_endpoint,
)
from tools.remote.config import load_remote_config
from tools.remote.upload_contract import RemoteUploadContractError


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

    def fake_collect_endpoint_components(*args):
        calls.append(("_agent_refs", args))
        return None, [ref]

    def fake_run_posture_rules(refs, manifests, settings_manifests, *, allowed_rules=None):
        calls.append(("run_posture_rules", refs))
        assert manifests == [("mcp", {})]
        assert settings_manifests == [("settings", {})]
        assert allowed_rules is None
        return [_posture("openaca-posture-mutable-install-reference")]

    monkeypatch.setattr("tools.remote.collector._agent_refs", fake_collect_endpoint_components)
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([("mcp", {})], [("settings", {})]),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", fake_run_posture_rules)

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    assert calls[0][0] == "_agent_refs"
    assert calls[1] == ("run_posture_rules", [ref])
    metadata_props = {p["name"]: p["value"] for p in collection.bom["metadata"]["properties"]}
    # The upload names no place: `openaca:target_type` is no longer written at
    # all, and `openaca:target` is dropped rather than carrying a literal
    # (plan 041; ADR-0051 covers what is left in metadata).
    assert "openaca:target_type" not in metadata_props
    assert "openaca:target" not in metadata_props
    assert collection.posture_findings == [
        {
            "source": "openaca",
            "source_version": "unknown",
            "finding_id": "openaca-posture-mutable-install-reference",
            "finding_version": "1",
            "severity": "LOW",
            "confidence": "high",
            "scope": "component",
            "summary": "Mutable install",
            "fix": "Pin the install reference.",
            "evidence": {"install_ref": "@example/mcp", "manifest_path": ".mcp.json"},
            "taxonomies": {},
            "source_specific": {
                "openaca": {"rule_id": "openaca-posture-mutable-install-reference"}
            },
        }
    ]


def test_build_endpoint_collections_respects_the_kind_posture_allowlist(tmp_path, monkeypatch):
    """A kind that restricts its posture rules must see that restriction
    honored remotely, exactly as the local `scan endpoint` path already does
    (`tools/scan.py` passes `allowed_rules=kind.posture_rules`)."""
    from dataclasses import replace as dc_replace

    import tools.agent_kinds as agent_kinds
    from tests.fixtures.agent_kinds import register_synthetic_kind

    def mcp_collector(config_root, project_root, refs):
        return [
            (
                config_root / ".mcp.json",
                {"mcpServers": {"example": {"url": "http://insecure.example"}}},
            )
        ]

    def settings_collector(config_root, project_root):
        return []

    kind = register_synthetic_kind(monkeypatch, agent_ids=["a"])
    kind = dc_replace(
        kind,
        posture_rules=frozenset(),
        installed_posture_collectors=(mcp_collector, settings_collector),
    )
    monkeypatch.setattr(agent_kinds, "REGISTRY", (kind,))

    collections = build_endpoint_collections(config_dir=tmp_path, project=None)

    assert len(collections) == 1
    assert collections[0].posture_findings == []


def test_build_endpoint_collection_uploads_external_scanner_findings(tmp_path, monkeypatch):
    ref = ComponentRef(
        component_identity="skill/deploy-helper",
        source_manifest="skills/deploy-helper/SKILL.md",
        source_locator="$.frontmatter",
        extra={"component_type": "skill", "name": "deploy-helper"},
    )

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    def fake_collect_skillspector_findings(refs):
        assert refs == [ref]
        return SkillSpectorFindings(
            observations=[
                ObservationFinding(
                    source="skillspector",
                    source_version="0.4.0",
                    observation_id="P1",
                    title="Instruction override",
                    severity="high",
                    confidence="medium",
                    component={
                        "identity": "skill/deploy-helper",
                        "name": "deploy-helper",
                        "type": "skill",
                    },
                    subject_coordinate="sha256:test",
                    evidence={"sarif_rule_id": "P1"},
                    categories=["prompt-injection"],
                    remediation="Review the instruction.",
                    declared_by={"kind": "sarif", "path": "skills/deploy-helper/SKILL.md"},
                )
            ],
            posture_findings=[
                PostureFinding(
                    source="skillspector",
                    source_version="0.4.0",
                    rule_id="LP2",
                    title="Wildcard permission",
                    severity="medium",
                    confidence="medium",
                    component={
                        "identity": "skill/deploy-helper",
                        "name": "deploy-helper",
                        "type": "skill",
                    },
                    active_in=[],
                    declared_by={"kind": "sarif", "path": "skills/deploy-helper/SKILL.md"},
                    component_path=[{"type": "skill", "name": "skill/deploy-helper"}],
                    standards=Standards(),
                    remediation="Review the declared permission.",
                    evidence={
                        "sarif_rule_id": "LP2",
                        "categories": ["privilege-escalation"],
                    },
                )
            ],
            warnings=[],
        )

    monkeypatch.setattr(
        "tools.remote.collector.collect_skillspector_findings",
        fake_collect_skillspector_findings,
    )

    collection = build_endpoint_collections(
        config_dir=tmp_path,
        project=None,
        external_scanners=("nvidia-skillspector",),
    )[0]

    assert collection.observations == [
        {
            "source": "skillspector",
            "source_version": "0.4.0",
            "finding_id": "skillspector:P1",
            "finding_version": "1",
            "severity": "HIGH",
            "confidence": "medium",
            "subject_coordinate": "sha256:test",
            "summary": "Instruction override",
            "fix": "Review the instruction.",
            "evidence": {},
            "taxonomies": {"openaca_categories": ["prompt-injection"]},
            "source_specific": {"skillspector": {"rule_id": "P1"}},
            "declared_by": {"kind": "sarif", "path": "skills/deploy-helper/SKILL.md"},
        }
    ]
    assert collection.posture_findings == [
        {
            "source": "skillspector",
            "source_version": "0.4.0",
            "finding_id": "skillspector:LP2",
            "finding_version": "1",
            "severity": "MEDIUM",
            "confidence": "medium",
            "scope": "component",
            "summary": "Wildcard permission",
            "fix": "Review the declared permission.",
            "evidence": {
                "manifest_path": "skills/deploy-helper/SKILL.md",
            },
            "taxonomies": {"openaca_categories": ["privilege-escalation"]},
            "source_specific": {"skillspector": {"rule_id": "LP2"}},
        }
    ]


def test_build_endpoint_collection_missing_external_scanner_aborts(tmp_path, monkeypatch):
    ref = ComponentRef(
        component_identity="skill/deploy-helper",
        source_manifest="skills/deploy-helper/SKILL.md",
        source_locator="$.frontmatter",
        extra={"component_type": "skill", "name": "deploy-helper"},
    )

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    def missing_collect(_refs):
        from tools.observations.skillspector import SkillSpectorCommandNotFound

        raise SkillSpectorCommandNotFound("SkillSpector command not found: skillspector")

    monkeypatch.setattr("tools.remote.collector.collect_skillspector_findings", missing_collect)

    with pytest.raises(CollectError, match="SkillSpector command not found: skillspector"):
        build_endpoint_collections(
            config_dir=tmp_path,
            project=None,
            external_scanners=("nvidia-skillspector",),
        )


def test_build_endpoint_collection_surfaces_scanner_warnings(tmp_path, monkeypatch, capsys):
    ref = ComponentRef(
        component_identity="skill/deploy-helper",
        source_manifest="skills/deploy-helper/SKILL.md",
        source_locator="$.frontmatter",
        extra={"component_type": "skill", "name": "deploy-helper"},
    )

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    monkeypatch.setattr(
        "tools.remote.collector.collect_skillspector_findings",
        lambda _refs: SkillSpectorFindings(
            observations=[],
            posture_findings=[],
            warnings=["SkillSpector timed out for skills/deploy-helper"],
        ),
    )

    build_endpoint_collections(
        config_dir=tmp_path,
        project=None,
        external_scanners=("nvidia-skillspector",),
    )

    captured = capsys.readouterr()
    assert "warning: SkillSpector timed out for skills/deploy-helper" in captured.err


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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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
            "bom_ref": "mcp-server/npm/@playwright/mcp",
        },
    )

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-server/npm/@playwright/mcp"
    assert collection.bom["components"][0]["bom-ref"] == "mcp-server/npm/@playwright/mcp"
    assert collection.posture_findings[0]["component_bom_ref"] == ("mcp-server/npm/@playwright/mcp")


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
            "bom_ref": "mcp-remote/example.com/mcp",
        },
    )

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([(manifest_path, manifest)], []),
    )
    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-remote/example.com/mcp"
    assert collection.bom["components"][0]["bom-ref"] == "mcp-remote/example.com/mcp"
    assert collection.posture_findings[0]["component_bom_ref"] == "mcp-remote/example.com/mcp"


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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:install_source"] == "uvx mcp-server==1.2.3"


def test_build_endpoint_collection_trims_binary_mcp_with_component_path(tmp_path, monkeypatch):
    # Source-less binary MCPs have no cross-BOM identity; install_source still
    # trims to the executable before upload.
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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert "openaca:identity" not in props
    assert props["openaca:install_source"] == "python"


def test_build_endpoint_collection_trims_local_mcp_with_component_path(tmp_path, monkeypatch):
    # Same as above for a local plugin launcher without a stable source.
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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert "openaca:identity" not in props
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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-server/npm/@scope/pkg"
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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-server/pypi/my-tool"
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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-server/pypi/my-tool"
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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-server/pypi/weather-mcp"
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
    # npx --package <pkg> cmd [...] installs <pkg> then runs cmd. For remote inventory the
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

    monkeypatch.setattr("tools.remote.collector._agent_refs", lambda *args: (None, [ref]))
    monkeypatch.setattr(
        "tools.remote.collector._agent_posture_manifests",
        lambda agent, refs: ([], []),
    )
    monkeypatch.setattr("tools.remote.collector.run_posture_rules", lambda *args, **kwargs: [])

    collection = build_endpoint_collections(config_dir=tmp_path, project=None)[0]

    props = {prop["name"]: prop["value"] for prop in collection.bom["components"][0]["properties"]}
    assert props["openaca:identity"] == "mcp-server/npm/@scope/pkg"
    assert props["openaca:install_source"] == expected


def test_collect_endpoint_registers_asset_uploads_bom_and_saves_asset_id(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id=None)
    pending_dir = tmp_path / "pending"
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr("tools.remote.collector.socket.gethostname", lambda: "demo-host")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
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

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    results = collect_endpoint(config_dir=tmp_path, project=None)

    assert results[0].asset_id == "asset-123"
    assert [name for name, _ in calls] == ["init", "register_asset", "upload_bom"]
    assert calls[1][1]["asset_type"] == "endpoint"
    assert calls[1][1]["external_id"] == "demo-host"
    assert calls[2][1]["asset_id"] == "asset-123"
    assert calls[2][1]["content_hash"].startswith("sha256:")
    assert calls[2][1]["posture_findings"][0]["rule_id"] == "openaca-posture-insecure-transport"
    assert load_remote_config(config_path).asset_id == "asset-123"


def test_collect_endpoint_forwards_external_scanners_to_collection(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-123")
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")

    def fake_build_endpoint_collection(**kwargs):
        calls.append(("build_endpoint_collections", kwargs))
        return [_collection()]

    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections", fake_build_endpoint_collection
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            calls.append(("upload_bom", payload))
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    collect_endpoint(
        config_dir=tmp_path,
        project=None,
        external_scanners=("nvidia-skillspector",),
    )

    assert calls[0] == (
        "build_endpoint_collections",
        {
            "config_dir": tmp_path,
            "project": None,
            "external_scanners": ("nvidia-skillspector",),
        },
    )
    assert calls[1][0] == "upload_bom"


def test_collect_endpoint_uploads_content_hash_of_redacted_bom(tmp_path, monkeypatch):
    """Remote's contract defines `content_hash = sha256(raw_bom)`. Before this
    fix, `_upload_payload` computed the hash, then `_redact_payload_for_remote`
    mutated `payload["bom"]` in place, so the wire payload carried a hash
    of the pre-redacted BOM while the backend stored the post-redacted BOM
    under that hash. This test reproduces the upload path with a dirty BOM
    (absolute path under config_dir) and asserts the hash on the wire
    matches the BOM on the wire.
    """
    from tools.remote.collector import _content_hash

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

    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(bom=dirty_bom)],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            captured["payload"] = payload
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    payload = captured["payload"]
    # The absolute path must have been redacted out of the BOM.
    redacted_value = payload["bom"]["components"][0]["properties"][0]["value"]
    assert not redacted_value.startswith(str(tmp_path)), redacted_value
    # And the content_hash field must equal sha256 of the (post-redaction)
    # BOM actually being uploaded — not the hash of some prior BOM state.
    assert payload["content_hash"] == _content_hash(payload["bom"])


def test_redact_payload_redacts_absolute_paths_in_observation_evidence_list(tmp_path):
    """List-valued evidence fields must be recursed during redaction.
    Without the fix an absolute path string inside a list would be uploaded unredacted.
    """
    from tools.remote.collector import _redact_payload_for_remote

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    abs_path = str(config_dir / "skills" / "deploy" / "SKILL.md")
    payload = {
        "bom": {"components": []},
        "posture_findings": [],
        "observations": [
            {
                "source": "openaca-skill-audit",
                "observation_id": "skill.suspicious-instruction",
                "evidence": {
                    "matched_text": [abs_path, "Read"],
                    "source_manifest": abs_path,
                },
            }
        ],
    }

    _redact_payload_for_remote(payload, config_dir=config_dir, project=None)

    evidence = payload["observations"][0]["evidence"]
    assert not evidence["matched_text"][0].startswith("/"), evidence["matched_text"]
    assert evidence["matched_text"][1] == "Read"
    assert not evidence["source_manifest"].startswith("/")


def test_redact_payload_redacts_embedded_absolute_path_in_bash_filter(tmp_path):
    """Absolute paths embedded inside Bash filter syntax must be redacted.
    `Bash(/config_dir/skills/deploy/run.sh *)` should become `Bash(skills/deploy/run.sh *)`
    (relativized under config_dir) while preserving the surrounding structure.
    """
    from tools.remote.collector import _redact_payload_for_remote

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    abs_path = str(config_dir / "skills" / "deploy" / "run.sh")
    payload = {
        "bom": {"components": []},
        "posture_findings": [],
        "observations": [
            {
                "source": "openaca-skill-audit",
                "observation_id": "skill.suspicious-instruction",
                "evidence": {
                    "matched_text": [f"Bash({abs_path} *)", "Read"],
                },
            }
        ],
    }

    _redact_payload_for_remote(payload, config_dir=config_dir, project=None)

    evidence = payload["observations"][0]["evidence"]
    tool = evidence["matched_text"][0]
    assert str(config_dir) not in tool, tool
    assert tool.startswith("Bash("), tool
    assert evidence["matched_text"][1] == "Read"


def test_redact_payload_preserves_url_in_embedded_bash_filter(tmp_path):
    """URLs embedded inside Bash filter syntax must be preserved (host-only) not corrupted.
    `Bash(curl https://api.example.com/mcp *)` should become
    `Bash(curl https://api.example.com *)` — URL path stripped, not mangled to `https:mcp`.
    """
    from tools.remote.collector import _redact_payload_for_remote

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    payload = {
        "bom": {"components": []},
        "posture_findings": [],
        "observations": [
            {
                "source": "openaca-skill-audit",
                "observation_id": "skill.suspicious-instruction",
                "evidence": {
                    "matched_text": ["Bash(curl https://api.example.com/mcp *)", "Read"],
                },
            }
        ],
    }

    _redact_payload_for_remote(payload, config_dir=config_dir, project=None)

    evidence = payload["observations"][0]["evidence"]
    tool = evidence["matched_text"][0]
    assert tool == "Bash(curl https://api.example.com *)", tool
    assert evidence["matched_text"][1] == "Read"


def test_redact_payload_redacts_file_uri_in_scanner_evidence(tmp_path):
    """file:// URIs produced by SARIF-based scanners (e.g. SkillSpector) must be
    relativized before upload. A URI like 'file:///Users/alice/.claude/skills/SKILL.md'
    is not caught by _is_absolute_path (doesn't start with '/') and would otherwise
    pass through both the redactor and the enforcer unchanged.
    """
    from tools.remote.collector import _redact_payload_for_remote

    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    skill_uri = f"file://{config_dir}/skills/deploy-helper/SKILL.md"

    payload = {
        "bom": {"components": []},
        "posture_findings": [
            {
                "evidence": {
                    "location_uri": skill_uri,
                    "manifest_path": skill_uri,
                }
            }
        ],
        "observations": [
            {
                "evidence": {"location_uri": skill_uri},
                "declared_by": {"kind": "sarif", "path": skill_uri},
            }
        ],
    }

    _redact_payload_for_remote(payload, config_dir=config_dir, project=None)

    obs = payload["observations"][0]
    assert obs["evidence"]["location_uri"] == "skills/deploy-helper/SKILL.md"
    assert obs["declared_by"]["path"] == "skills/deploy-helper/SKILL.md"
    assert obs["declared_by"]["kind"] == "sarif"

    pf = payload["posture_findings"][0]
    assert pf["evidence"]["location_uri"] == "skills/deploy-helper/SKILL.md"
    assert pf["evidence"]["manifest_path"] == "skills/deploy-helper/SKILL.md"


def test_collect_endpoint_uses_existing_asset_id(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    calls: list[str] = []
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            calls.append("init")

        def register_asset(self, payload):
            raise AssertionError("asset should not be re-registered")

        def upload_bom(self, payload):
            calls.append(payload["asset_id"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert calls == ["init", "asset-existing"]


def test_collect_endpoint_caches_payload_on_interactive_offline_failure(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert exc.value.exit_code == 2
    pending = list(pending_dir.glob("pending-bom-*.json"))
    assert len(pending) == 1
    cached = json.loads(pending[0].read_text(encoding="utf-8"))
    assert cached["asset_id"] == "asset-existing"


def test_collect_endpoint_converts_upload_client_error_to_collect_error(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise RemoteAuthError("invalid or revoked token")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert exc.value.exit_code == 1
    assert str(exc.value) == "invalid or revoked token"


def test_collect_endpoint_converts_registration_network_error_to_collect_error(
    tmp_path, monkeypatch
):
    config_path = _write_config(tmp_path, asset_id=None)
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def register_asset(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as exc:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert exc.value.exit_code == 2
    assert "asset registration failed" in str(exc.value)


def test_collect_endpoint_redacts_absolute_paths_before_upload(tmp_path, monkeypatch):
    """ADR 0003: the CLI redacts absolute paths before upload so the Remote
    backend's redaction check passes. Paths under config_dir are
    relativized; paths under an unknown root fall back to basename.
    """
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    uploads: list[dict[str, Any]] = []
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")

    # Two openaca:* properties: one inside the test's config_dir (tmp_path)
    # which should relativize, and one outside which should fall back to
    # basename.
    inside = tmp_path / "skills" / "x" / "SKILL.md"
    outside = "/Users/alex/.claude/settings.json"
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [
            _collection(
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
            )
        ],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    props = uploads[0]["bom"]["components"][0]["properties"]
    # Inside config_dir → relativized; outside config_dir → basename + stable digest
    # (openaca:source_manifest disambiguator, parity with the bom-ref redaction).
    outside_digest = hashlib.sha256(outside.encode()).hexdigest()[:8]
    assert props[0]["value"] == "skills/x/SKILL.md"
    assert props[1]["value"] == f"settings.json.{outside_digest}"


def test_write_pending_payload_creates_file_mode_0600(tmp_path, monkeypatch):
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError):
        collect_endpoint(config_dir=tmp_path, project=None)

    pending = list(pending_dir.glob("pending-bom-*.json"))
    assert len(pending) == 1
    assert stat.S_IMODE(os.stat(pending[0]).st_mode) == 0o600


def test_collect_endpoint_quiet_offline_failure_exits_zero_after_cache(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    results = collect_endpoint(config_dir=tmp_path, project=None, quiet=True)

    # `--quiet` gates only the cached-failure category, so nothing raises and
    # the CLI still exits 0 — as before, when this raised CollectError(exit_code=0).
    assert results == []
    assert len(list((tmp_path / "pending").glob("pending-bom-*.json"))) == 1


def test_collect_endpoint_replays_pending_cache_before_current_upload(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    old_payload = _payload(asset_id="asset-existing", content_hash="sha256:old")
    (pending_dir / "pending-bom-1.json").write_text(json.dumps(old_payload), encoding="utf-8")
    uploads: list[str] = []
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload["content_hash"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

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
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [(collection_built.append(True), _collection())[1]],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    results = collect_endpoint(config_dir=tmp_path, project=None, allow_offline_cache=True)

    # `--allow-offline-cache` gates only the cached-failure category, so this
    # returns instead of raising CollectError(exit_code=0); the CLI exits 0 either way.
    assert results == []
    assert collection_built, "current endpoint collection must run even when replay fails"
    assert (pending_dir / "pending-bom-1.json").exists(), "old pending file kept for next attempt"
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 2, "new pending file written"


def test_collect_endpoint_skips_and_removes_corrupt_pending_file(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    pending_dir = tmp_path / "pending"
    pending_dir.mkdir()
    (pending_dir / "pending-bom-bad.json").write_text("not-json!!!", encoding="utf-8")

    uploads: list[str] = []
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload["content_hash"])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

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
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def register_asset(self, payload):
            from tools.remote.client import RegisterAssetResult

            return RegisterAssetResult(
                asset_id="new-asset-id", dashboard_url="https://app/assets/new-asset-id"
            )

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

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
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection()],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 1, "only the current upload ran, not the stale pending one"
    assert uploads[0]["asset_id"] == "new-asset-id"
    assert not stale_file.exists(), "stale pending file purged because asset_id mismatched"


def test_collect_endpoint_cli_prints_upload_summary(tmp_path, monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_collect_endpoint(**kwargs):
        calls.append(kwargs)
        return [_upload_result(asset_id="asset-123")]

    monkeypatch.setattr("tools.remote.cli.collect_endpoint", fake_collect_endpoint)

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
            "external_scanners": (),
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
        'api_url = "http://remote.test"',
        'token = "ot_TEST"',
    ]
    if asset_id is not None:
        lines.append(f'asset_id = "{asset_id}"')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def _collection(
    *, agent_kind: str = "claude-code", bom: dict[str, Any] | None = None
) -> EndpointCollection:
    return EndpointCollection(
        agent=AgentInstance(
            kind_id=agent_kind,
            display_name=agent_kind,
            source="installed",
            root_label=agent_kind,
            coverage_baseline="complete",
        ),
        bom=bom
        or {
            "bomFormat": "CycloneDX",
            "specVersion": "1.7",
            "components": [],
            "metadata": {"component": {"bom-ref": f"root/{agent_kind}"}},
        },
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
        observations=[],
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


def test_upload_contract_accepts_an_agent_rooted_document():
    """An agent id is new content in a document, so it is tested against the
    redaction contract rather than assumed safe. `build_agent_bom`'s flat
    (`graph=None`) path never sets `target_bom_ref`, so agent metadata only
    exists on a graph-rooted document — build one."""
    from tools.bom import build_agent_bom
    from tools.graph import Graph, Node
    from tools.remote.upload_contract import enforce_remote_upload_contract

    root = Node(key="root/synthetic/payments-triage", kind="target", ref=None)
    graph = Graph(nodes={root.key: root})

    doc = build_agent_bom(
        [],
        target_type=None,
        graph=graph,
        agent_kind="synthetic",
        agent_id="payments-triage",
        agent_name="payments-triage",
        composition_source="installed",
        composition_coverage="partial",
    ).to_cyclonedx()

    props = {p["name"]: p["value"] for p in doc["metadata"]["component"]["properties"]}
    assert doc["metadata"]["component"]["bom-ref"] == "root/synthetic/payments-triage"
    assert props["openaca:agent_kind"] == "synthetic"
    assert props["openaca:agent_id"] == "payments-triage"

    enforce_remote_upload_contract({"bom": doc})  # must not raise


def test_dry_run_builds_the_payload_that_would_be_uploaded(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections", lambda **kwargs: [_collection()]
    )

    payloads = build_endpoint_dry_run_payloads(config_dir=tmp_path, project=None)

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["asset_id"] == "asset-existing"
    assert payload["source"] == "endpoint"
    assert payload["target_locator"] == "endpoint:user-scope"
    assert payload["content_hash"] == _content_hash_of(payload["bom"])
    assert payload["posture_findings"][0]["rule_id"] == "openaca-posture-insecure-transport"


def test_dry_run_never_constructs_a_remote_client(tmp_path, monkeypatch):
    """The point of a dry run is that nothing leaves the machine — including
    asset registration, which the upload path performs before its first upload."""
    config_path = _write_config(tmp_path, asset_id=None)
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections", lambda **kwargs: [_collection()]
    )

    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("dry run performed network I/O")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", fail)

    build_endpoint_dry_run_payloads(config_dir=tmp_path, project=None)


def test_dry_run_marks_an_unregistered_asset_rather_than_inventing_one(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id=None)
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections", lambda **kwargs: [_collection()]
    )

    payloads = build_endpoint_dry_run_payloads(config_dir=tmp_path, project=None)

    assert payloads[0]["asset_id"] == DRY_RUN_UNREGISTERED_ASSET_ID


def test_dry_run_works_without_remote_configuration(tmp_path, monkeypatch):
    """Previewing what a sync would send needs no token: nothing is sent.
    Requiring one would gate the preview on the step it exists to precede."""
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: tmp_path / "absent.toml")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections", lambda **kwargs: [_collection()]
    )

    payloads = build_endpoint_dry_run_payloads(config_dir=tmp_path, project=None)

    assert payloads[0]["asset_id"] == DRY_RUN_UNREGISTERED_ASSET_ID


def test_dry_run_writes_no_config_and_no_pending_cache(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path, asset_id=None)
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections", lambda **kwargs: [_collection()]
    )
    before = config_path.read_text(encoding="utf-8")

    build_endpoint_dry_run_payloads(config_dir=tmp_path, project=None)

    assert config_path.read_text(encoding="utf-8") == before
    assert not pending_dir.exists()


def test_dry_run_shows_the_redacted_payload_not_the_raw_one(tmp_path, monkeypatch):
    """A dry run that printed pre-redaction values would misrepresent what
    crosses the boundary — the exact thing a user runs it to check."""
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "components": [
            {
                "bom-ref": "component-1",
                "name": "example",
                "properties": [
                    {
                        "name": "openaca:source_manifest",
                        "value": str(tmp_path / "skills" / "deploy" / "SKILL.md"),
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections", lambda **kwargs: [_collection(bom=bom)]
    )

    payloads = build_endpoint_dry_run_payloads(config_dir=tmp_path, project=None)

    value = payloads[0]["bom"]["components"][0]["properties"][0]["value"]
    assert value == "skills/deploy/SKILL.md"


def test_dry_run_enforces_the_upload_contract_rather_than_printing_a_violation(
    tmp_path, monkeypatch
):
    config_path = _write_config(tmp_path, asset_id="asset-existing")
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "components": [
            {
                "bom-ref": "component-1",
                "name": "example",
                "properties": [{"name": "openaca:env", "value": "anything"}],
            }
        ],
    }
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections", lambda **kwargs: [_collection(bom=bom)]
    )

    with pytest.raises(RemoteUploadContractError):
        build_endpoint_dry_run_payloads(config_dir=tmp_path, project=None)


def _content_hash_of(bom: dict[str, Any]) -> str:
    payload = json.dumps(bom, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


# --- per-agent collection (plan 041 Task 2) ----------------------------------


def _endpoint_fixture(root: Path) -> Path:
    skill = root / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )
    return root


def test_build_endpoint_collections_emits_one_agent_rooted_bom_per_agent(tmp_path):
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collections = build_endpoint_collections(config_dir=config_dir, project=None)

    assert len(collections) == 1
    assert collections[0].agent.kind_id == "claude-code"
    metadata = collections[0].bom["metadata"]
    props = {p["name"]: p["value"] for p in metadata["properties"]}
    assert props["openaca:schema_version"] == "0.5"
    assert "openaca:target_type" not in props
    assert "openaca:target" not in props
    assert metadata["component"]["bom-ref"] == "root/claude-code"
    component_props = {p["name"]: p["value"] for p in metadata["component"]["properties"]}
    assert component_props["openaca:agent_kind"] == "claude-code"
    assert component_props["openaca:composition_source"] == "installed"
    assert "openaca:agent_id" not in component_props


# --- per-agent upload (plan 041 Task 3) --------------------------------------


def test_collect_endpoint_uploads_one_payload_per_agent(tmp_path, monkeypatch):
    """Same asset_id in every envelope; the agent is named inside the
    document (ADR-0050)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    results = collect_endpoint(config_dir=tmp_path, project=None)

    assert len(results) == 2
    assert [u["asset_id"] for u in uploads] == ["asset-123", "asset-123"]
    assert [u["target_locator"] for u in uploads] == ["endpoint:user-scope"] * 2
    assert uploads[0]["content_hash"] != uploads[1]["content_hash"]


def test_collect_endpoint_caches_only_the_failing_agent(tmp_path, monkeypatch):
    """A network failure on one agent must not discard the others (ADR-0050)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            self.calls = 0

        def upload_bom(self, payload):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("down")
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    results = collect_endpoint(config_dir=tmp_path, project=None, allow_offline_cache=True)

    assert len(results) == 1
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 1


def test_collect_endpoint_attempts_every_agent_by_default_and_names_the_failed_one(
    tmp_path, monkeypatch
):
    """Default mode (neither `--quiet` nor `--allow-offline-cache`) must still
    attempt every discovered agent after an earlier one fails on the network,
    and the raised error must identify which agent(s) it could not upload
    (spec: "reports which ones it could not"; ADR-0050: per-agent independence)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            if len(uploads) == 1:
                raise httpx.ConnectError("down")
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 2  # the second agent was still attempted
    assert excinfo.value.exit_code == 2
    assert "root/claude-code" in str(excinfo.value)
    assert "root/other" not in str(excinfo.value)
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 1


def test_collect_endpoint_warns_and_returns_empty_when_no_agent_discovered(tmp_path, monkeypatch):
    """Matches `scan endpoint`'s convention (`tools/scan.py`) for the same
    condition, rather than leaving the outcome of zero discovered agents
    unspecified."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr("tools.remote.collector.build_endpoint_collections", lambda **kwargs: [])

    results = collect_endpoint(config_dir=tmp_path, project=None)

    assert results == []


def test_collect_endpoint_attempts_every_agent_after_multiple_network_failures(
    tmp_path, monkeypatch
):
    """Two retryable failures in a three-agent sync must not stop at the
    first or second — every agent is still attempted, and every failure is
    cached and named."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [
            _collection(agent_kind="claude-code"),
            _collection(agent_kind="other"),
            _collection(agent_kind="third"),
        ],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            if len(uploads) in (1, 3):
                raise httpx.ConnectError("down")
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 3  # every agent was attempted, including after the second failure
    assert excinfo.value.exit_code == 2
    assert "root/claude-code" in str(excinfo.value)
    assert "root/third" in str(excinfo.value)
    assert "root/other" not in str(excinfo.value)
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 2


def test_collect_endpoint_continues_past_a_rejected_agent_without_caching_it(tmp_path, monkeypatch):
    """A 422 or 413 rejects one agent's document, not the connection or the
    token — the next agent's document is unrelated and must still be
    attempted, and the rejected one is not cached (`--allow-offline-cache`'s
    own scope is a pending cache file, and retrying an invalid payload
    unchanged would only be rejected again)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            if len(uploads) == 1:
                raise RemoteValidationError("document too large for one agent", [])
            return _upload_result(asset_id=payload["asset_id"])

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None, allow_offline_cache=True)

    assert len(uploads) == 2  # the second agent was still attempted
    assert (
        excinfo.value.exit_code == 1
    )  # not suppressed by --allow-offline-cache: nothing was cached
    assert "root/claude-code" in str(excinfo.value)
    assert "root/other" not in str(excinfo.value)
    assert list(pending_dir.glob("pending-bom-*.json")) == []


def test_collect_endpoint_names_both_a_rejected_and_a_cached_agent_together(tmp_path, monkeypatch):
    """A rejection and a network failure in the same sync must not lose one
    of them: the rejected list short-circuiting past the cached list would
    silently drop whichever agent it didn't raise about. `--quiet` is set
    here specifically because it suppresses the per-agent echoes above —
    the final exception is the only place left for either agent's name to
    appear, so it must name both."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    pending_dir = tmp_path / "pending"
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: pending_dir)
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [
            _collection(agent_kind="claude-code"),
            _collection(agent_kind="other"),
            _collection(agent_kind="third"),
        ],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            if len(uploads) == 1:
                return _upload_result(asset_id=payload["asset_id"])
            if len(uploads) == 2:
                raise RemoteValidationError("document too large for one agent", [])
            raise httpx.ConnectError("down")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None, quiet=True)

    assert len(uploads) == 3  # every agent was attempted despite the rejection
    assert excinfo.value.exit_code == 1  # a rejection is present, so not suppressed
    assert "root/claude-code" not in str(excinfo.value)  # the succeeding agent
    assert "root/other" in str(excinfo.value)  # rejected
    assert "root/third" in str(excinfo.value)  # cached
    assert len(list(pending_dir.glob("pending-bom-*.json"))) == 1


def test_collect_endpoint_aborts_on_auth_failure_without_attempting_later_agents(
    tmp_path, monkeypatch
):
    """One token authenticates every upload in a sync; a rejected token will
    reject every remaining agent too, so this stays a global, immediate
    abort rather than a per-agent failure (unlike the network/validation
    cases above, which keep attempting the remaining agents)."""
    config_path = _write_config(tmp_path, asset_id="asset-123")
    monkeypatch.setattr("tools.remote.collector.get_config_path", lambda: config_path)
    monkeypatch.setattr("tools.remote.collector.get_pending_dir", lambda: tmp_path / "pending")
    monkeypatch.setattr(
        "tools.remote.collector.build_endpoint_collections",
        lambda **kwargs: [_collection(agent_kind="claude-code"), _collection(agent_kind="other")],
    )
    uploads: list[dict] = []

    class FakeClient:
        def __init__(self, *, api_url: str, token: str) -> None:
            pass

        def upload_bom(self, payload):
            uploads.append(payload)
            raise RemoteAuthError("invalid or revoked token")

    monkeypatch.setattr("tools.remote.collector.RemoteClient", FakeClient)

    with pytest.raises(CollectError) as excinfo:
        collect_endpoint(config_dir=tmp_path, project=None)

    assert len(uploads) == 1  # the second agent was never attempted
    assert excinfo.value.exit_code == 1
    assert str(excinfo.value) == "invalid or revoked token"
