"""Native app IDs bind runtime namespaces to their installed plugin owners."""

import json

import pytest

from openaca.core import apply_risk_gates, collect_policy_evidence, parse_policy
from tools.graph_build import build_codex_installed_graph


def endpoint(tmp_path, *, marker=True):
    root = tmp_path / "codex"
    plugin = root / "plugins/cache/openai-curated-remote/github/1.0.0"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "github", "version": "1.0.0", "apps": "./.app.json"})
    )
    # The reference alias deliberately differs from the runtime namespace.
    (plugin / ".app.json").write_text(
        json.dumps({"apps": {"company-code": {"id": "connector_github", "required": True}}})
    )
    if marker:
        (plugin.parent / ".codex-remote-plugin-install.json").write_text(
            json.dumps({"schema_version": 1, "remote_plugin_id": "plugin_connector_github"})
        )
    cache = root / "cache/codex_apps_tools"
    cache.mkdir(parents=True)
    (cache / "account.json").write_text(
        json.dumps(
            {
                "schema_version": 4,
                "tools": [
                    {
                        "server_name": "codex_apps",
                        "connector_id": "connector_github",
                        "tool_namespace": "codex_apps__github",
                        "tool_name": "_fetch_file",
                    },
                    {
                        "server_name": "codex_apps",
                        "connector_id": "connector_other",
                        "tool_namespace": "codex_apps__other",
                        "tool_name": "_fetch_file",
                    },
                ],
            }
        )
    )
    return root, plugin, cache


def test_remote_plugin_and_app_are_inventoried_with_one_actionable_owner(tmp_path):
    root, _, _ = endpoint(tmp_path)
    graph = build_codex_installed_graph(root)
    plugin = next(n for n in graph.nodes.values() if n.kind == "plugin")
    assert plugin.ref is not None
    assert plugin.ref.extra["enabled"] is True
    assert plugin.ref.extra["marketplace"] == "openai-curated-remote"
    assert plugin.ref.component_identity == "plugin/openai-curated-remote/github"
    apps = [n for n in graph.children_of(plugin) if n.kind == "mcp_server"]
    assert len(apps) == 1
    assert apps[0].ref is not None
    assert apps[0].ref.extra["codex_app_namespaces"] == ["codex_apps__github"]
    assert apps[0].ref.extra["transport"] == "codex-app"
    assert "url" not in apps[0].ref.extra and "mcp_command" not in apps[0].ref.extra


@pytest.mark.parametrize("outcome", ["allowed", "blocked"])
def test_app_inherits_exact_plugin_decision_with_opposite_mcp_default(tmp_path, outcome):
    root, _, _ = endpoint(tmp_path)
    policy = parse_policy(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "blocked" if outcome == "allowed" else "allowed"},
                "plugins": {
                    "default": "blocked" if outcome == "allowed" else "allowed",
                    outcome: [{"plugin": "github@openai-curated-remote"}],
                },
                "skills": {"default": "allowed"},
            },
        }
    )
    evidence = collect_policy_evidence(
        policy, target=root, project=None, agent_kind="codex", allow_partial_inventory=True
    )
    decisions = apply_risk_gates(
        policy, list(evidence.components), advisories=[], advisory_matches=[], posture_matches=[]
    )
    app = next(d for d in decisions if d.ref.extra.get("transport") == "codex-app")
    assert app.controlled_by_plugin
    assert app.subject.ref.name == "github"
    assert app.blocked is (outcome == "blocked")


def test_cache_directory_alone_does_not_grant_remote_plugin_identity(tmp_path):
    root, _, _ = endpoint(tmp_path, marker=False)
    graph = build_codex_installed_graph(root)
    plugin = next(n for n in graph.nodes.values() if n.kind == "plugin")
    assert plugin.ref is not None
    assert plugin.ref.extra["enabled"] is False
    assert "marketplace" not in plugin.ref.extra


def test_missing_tool_cache_keeps_app_inventory_and_reports_gap(tmp_path):
    root, _, cache = endpoint(tmp_path)
    (cache / "account.json").unlink()
    graph = build_codex_installed_graph(root)
    apps = [
        n for n in graph.nodes.values() if n.ref and n.ref.extra.get("transport") == "codex-app"
    ]
    assert len(apps) == 1
    assert apps[0].ref is not None
    assert apps[0].ref.extra.get("codex_app_namespaces", []) == []
    assert any("app namespace" in gap for gap in graph.warnings.gaps)


@pytest.mark.parametrize(
    "record",
    [
        [],
        {"schema_version": 2, "remote_plugin_id": "plugin_x"},
        {"schema_version": 1, "remote_plugin_id": "connector_x"},
    ],
)
def test_invalid_installation_records_do_not_grant_a_marketplace_identity(tmp_path, record):
    root, plugin, _ = endpoint(tmp_path)
    (plugin.parent / ".codex-remote-plugin-install.json").write_text(json.dumps(record))
    graph = build_codex_installed_graph(root)
    owner = next(n.ref for n in graph.nodes.values() if n.kind == "plugin")
    assert owner is not None
    assert owner.extra["enabled"] is False
    assert "marketplace" not in owner.extra
    assert any("remote plugin installation" in gap for gap in graph.warnings.gaps)


def test_conflicting_native_app_ids_cannot_authorize_a_shared_namespace(tmp_path):
    root, _, cache = endpoint(tmp_path)
    data = json.loads((cache / "account.json").read_text())
    data["tools"][1]["tool_namespace"] = "codex_apps__github"
    (cache / "account.json").write_text(json.dumps(data))
    graph = build_codex_installed_graph(root)
    app = next(
        n.ref for n in graph.nodes.values() if n.ref and n.ref.extra.get("transport") == "codex-app"
    )
    assert app is not None
    assert app.extra["codex_app_namespaces"] == []


def test_explicit_disable_is_not_overridden_by_remote_installation(tmp_path):
    root, _, _ = endpoint(tmp_path)
    (root / "config.toml").write_text('[plugins."github@openai-curated-remote"]\nenabled=false\n')
    graph = build_codex_installed_graph(root)
    assert all(
        n.ref.extra.get("enabled") is False
        for n in graph.nodes.values()
        if n.ref and n.ref.extra.get("component_type") in {"plugin", "mcp_server"}
    )


def test_app_manifest_cannot_escape_the_plugin_root(tmp_path):
    root, plugin, _ = endpoint(tmp_path)
    (plugin / ".codex-plugin/plugin.json").write_text(
        json.dumps({"name": "github", "apps": "../../../../outside.json"})
    )
    graph = build_codex_installed_graph(root)
    assert not any(
        n.ref and n.ref.extra.get("transport") == "codex-app" for n in graph.nodes.values()
    )
    assert any("app references" in gap for gap in graph.warnings.gaps)


def test_exported_inventory_preserves_app_identity_namespace_and_plugin_ownership(tmp_path):
    from tools.bom import build_agent_bom, graph_from_cyclonedx

    root, _, _ = endpoint(tmp_path)
    graph = build_codex_installed_graph(root)
    exported = build_agent_bom([], graph=graph).to_cyclonedx()
    restored = graph_from_cyclonedx(exported)
    app = next(
        n for n in restored.nodes.values() if n.ref and n.ref.extra.get("transport") == "codex-app"
    )
    assert app.ref is not None
    assert app.ref.extra["codex_app_id"] == "connector_github"
    assert app.ref.extra["codex_app_namespaces"] == ["codex_apps__github"]
    owners = [n for n in restored.nodes.values() if app in restored.children_of(n)]
    assert len(owners) == 1
    assert owners[0].ref is not None
    assert owners[0].ref.component_identity == "plugin/openai-curated-remote/github"
