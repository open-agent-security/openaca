from __future__ import annotations

import json
from dataclasses import replace

import pytest
from click.testing import CliRunner

from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, Node
from tools.policy import EndpointComponent, PolicyValidationError, apply_risk_gates, parse
from tools.policy_claude import compile_policy
from tools.policy_cli import main as policy_main


def _graph_with_plugin_child(plugin: ComponentRef, child: ComponentRef, child_kind: str) -> Graph:
    root = Node(key="target", kind="target", ref=None)
    plugin_node = Node(key="plugin", kind="plugin", ref=plugin)
    child_node = Node(key="child", kind=child_kind, ref=child)
    return Graph(
        nodes={"target": root, "plugin": plugin_node, "child": child_node},
        edges=[Edge(parent="target", child="plugin"), Edge(parent="plugin", child="child")],
    )


def _policy(**risk_gates: object):
    return parse(
        {
            "version": 1,
            "admission": {
                "mcps": {
                    "default": "blocked",
                    "allowed": [{"command": ["npx", "-y", "safe-mcp"]}],
                },
                "plugins": {"default": "allowed"},
                "skills": {"default": "allowed"},
            },
            **({"risk_gates": risk_gates} if risk_gates else {}),
        }
    )


def _mcp(command: list[str]) -> ComponentRef:
    return ComponentRef(extra={"component_type": "mcp_server", "mcp_command": command})


def test_policy_requires_exact_v1_shape():
    with pytest.raises(PolicyValidationError, match="unsupported"):
        parse({"version": 1, "admission": {}, "unknown": True})


def test_policy_rejects_target_in_both_lists():
    with pytest.raises(PolicyValidationError, match="both allowed and blocked"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {
                        "default": "allowed",
                        "allowed": [{"url": "https://example.com/mcp"}],
                        "blocked": [{"url": "https://example.com/mcp"}],
                    },
                    "plugins": {"default": "allowed"},
                    "skills": {"default": "allowed"},
                },
            }
        )


def test_exact_mcp_command_is_allowed_and_other_command_follows_default():
    policy = _policy()
    allowed, blocked = _mcp(["npx", "-y", "safe-mcp"]), _mcp(["npx", "unsafe-mcp"])

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(allowed), EndpointComponent(blocked)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )

    assert [decision.blocked for decision in decisions] == [False, True]


def test_plugin_marketplace_block_wins_over_exact_allow():
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {
                    "default": "blocked",
                    "allowed": [{"plugin": "safe@internal"}],
                    "blocked": [{"marketplace": "https://marketplace.example/internal.git"}],
                },
                "skills": {"default": "allowed"},
            },
        }
    )
    plugin = ComponentRef(
        name="safe",
        extra={
            "component_type": "plugin",
            "marketplace": "internal",
            "marketplace_source": "https://marketplace.example/internal.git",
        },
    )

    decision = apply_risk_gates(
        policy,
        [EndpointComponent(plugin)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )[0]

    assert decision.blocked is True
    assert decision.reasons == ("admission allowed", "admission blocked")


def test_risk_gate_on_plugin_child_blocks_the_owning_plugin():
    policy = _policy(vulnerabilities={"ids": ["CVE-2026-12345"]})
    plugin = ComponentRef(
        name="bundle", extra={"component_type": "plugin", "marketplace": "internal"}
    )
    mcp = _mcp(["npx", "-y", "safe-mcp"])
    graph = _graph_with_plugin_child(plugin, mcp, "mcp_server")

    # Mirror `_refs_from_graph`: the scan's flat ref list holds
    # `dataclasses.replace` copies, not the graph's own Node.ref objects.
    plugin_copy = replace(plugin, extra={**plugin.extra, "bom_ref": "plugin"})
    mcp_copy = replace(mcp, extra={**mcp.extra, "bom_ref": "child"})

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(plugin_copy, graph), EndpointComponent(mcp_copy, graph)],
        advisories=[{"id": "GHSA-1234", "aliases": ["CVE-2026-12345"]}],
        advisory_matches=[(mcp_copy, "GHSA-1234")],
        posture_matches=[],
    )

    assert decisions[0].category == "plugins"
    assert decisions[0].blocked is True
    assert decisions[0].reasons[-1] == "vulnerability GHSA-1234"
    assert decisions[1].category == "mcps"
    assert decisions[1].blocked is False


def test_skill_bundled_in_an_allowed_plugin_is_not_blocked_by_skills_default():
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {"default": "allowed"},
                "skills": {"default": "blocked"},
            },
        }
    )
    plugin = ComponentRef(
        name="bundle", extra={"component_type": "plugin", "marketplace": "internal"}
    )
    skill = ComponentRef(name="helper", extra={"component_type": "skill"})
    graph = _graph_with_plugin_child(plugin, skill, "skill")

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(plugin, graph), EndpointComponent(skill, graph)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )

    assert decisions[0].blocked is False
    assert decisions[1].blocked is False
    assert decisions[1].reasons == ("owning plugin: plugins default: allowed",)


def test_skill_bundled_in_a_blocked_plugin_is_blocked_despite_allowed_skills_default():
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {"default": "blocked"},
                "skills": {"default": "allowed"},
            },
        }
    )
    plugin = ComponentRef(
        name="bundle", extra={"component_type": "plugin", "marketplace": "internal"}
    )
    skill = ComponentRef(name="helper", extra={"component_type": "skill"})
    graph = _graph_with_plugin_child(plugin, skill, "skill")

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(plugin, graph), EndpointComponent(skill, graph)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )

    assert decisions[0].blocked is True
    assert decisions[1].blocked is True


def test_standalone_skill_still_follows_skills_default():
    policy = _policy()
    skill = ComponentRef(name="helper", extra={"component_type": "skill"})

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(skill)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )

    assert decisions[0].blocked is False
    assert decisions[0].reasons == ("skills default: allowed",)


def test_vulnerability_gate_matches_an_advisory_alias():
    policy = _policy(vulnerabilities={"ids": ["CVE-2026-12345"]})
    mcp = _mcp(["npx", "-y", "safe-mcp"])

    decision = apply_risk_gates(
        policy,
        [EndpointComponent(mcp)],
        advisories=[{"id": "GHSA-1234", "aliases": ["CVE-2026-12345"]}],
        advisory_matches=[(mcp, "GHSA-1234")],
        posture_matches=[],
    )[0]

    assert decision.blocked is True
    assert decision.reasons[-1] == "vulnerability GHSA-1234"


def test_posture_gate_adds_an_exact_mcp_block():
    policy = _policy(posture={"rules": ["openaca-posture-insecure-transport"]})
    mcp = _mcp(["npx", "-y", "safe-mcp"])

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(mcp)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[(mcp, "openaca-posture-insecure-transport")],
    )
    compilation = compile_policy(policy, decisions)

    assert decisions[0].blocked is True
    assert compilation.settings["deniedMcpServers"] == [
        {"serverCommand": ["npx", "-y", "safe-mcp"]}
    ]


def test_claude_compiler_emits_only_restrictions():
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {
                    "default": "blocked",
                    "allowed": [{"command": ["npx", "-y", "safe-mcp"]}],
                    "blocked": [{"url": "https://blocked.example/mcp"}],
                },
                "plugins": {
                    "default": "allowed",
                    "blocked": [{"plugin": "unsafe@third-party"}],
                },
                "skills": {"default": "blocked"},
            },
        }
    )
    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(_mcp(["npx", "-y", "safe-mcp"]))],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )

    compilation = compile_policy(policy, decisions)

    assert compilation.settings == {
        "allowManagedMcpServersOnly": True,
        "allowedMcpServers": [{"serverCommand": ["npx", "-y", "safe-mcp"]}],
        "deniedMcpServers": [{"serverUrl": "https://blocked.example/mcp"}],
        "enabledPlugins": {"unsafe@third-party": False},
        "strictPluginOnlyCustomization": ["skills"],
    }
    assert compilation.limitations == ()


def test_policy_cli_validates_and_compiles_dry_run_json(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: blocked
    allowed:
      - command: [npx, -y, safe-mcp]
  plugins:
    default: allowed
  skills:
    default: allowed
"""
    )
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"safe":{"command":"npx","args":["-y","safe-mcp"]}}}'
    )
    runner = CliRunner()

    valid = runner.invoke(policy_main, ["validate", str(policy_path)])
    result = runner.invoke(
        policy_main,
        [
            "compile",
            str(policy_path),
            "--target",
            str(tmp_path),
            "--host",
            "claude",
            "--dry-run",
            "--format",
            "json",
            "--managed-settings-dir",
            str(tmp_path / "managed"),
        ],
    )

    assert valid.exit_code == 0, valid.output
    assert valid.output == ""
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["artifact"]["written"] is False
    assert report["expected_policy"]["allowManagedMcpServersOnly"] is True


def test_policy_cli_refuses_to_merge_a_generated_managed_key(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: blocked
  plugins:
    default: allowed
  skills:
    default: allowed
"""
    )
    (tmp_path / "settings.json").write_text("{}")
    managed = tmp_path / "managed" / "managed-settings.d"
    managed.mkdir(parents=True)
    (managed / "10-security.json").write_text('{"allowManagedMcpServersOnly": true}')
    output = tmp_path / "artifact.json"

    result = CliRunner().invoke(
        policy_main,
        [
            "compile",
            str(policy_path),
            "--target",
            str(tmp_path),
            "--host",
            "claude",
            "--output",
            str(output),
            "--managed-settings-dir",
            str(tmp_path / "managed"),
        ],
    )

    assert result.exit_code == 1
    assert "managed settings key collision" in result.output
    assert not output.exists()


def test_policy_cli_blocks_an_installed_plugin_from_a_blocked_marketplace(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
    blocked:
      - marketplace: https://github.com/acme/untrusted-plugins.git
  skills:
    default: allowed
"""
    )
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"unsafe@untrusted": True},
                "extraKnownMarketplaces": {
                    "untrusted": {"source": {"source": "github", "repo": "acme/untrusted-plugins"}}
                },
            }
        )
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "unsafe@untrusted": [
                        {"scope": "user", "version": "1.0", "installPath": "/missing"}
                    ]
                }
            }
        )
    )

    result = CliRunner().invoke(
        policy_main,
        [
            "compile",
            str(policy_path),
            "--target",
            str(tmp_path),
            "--host",
            "claude",
            "--dry-run",
            "--format",
            "json",
            "--managed-settings-dir",
            str(tmp_path / "managed"),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["expected_policy"]["enabledPlugins"] == {"unsafe@untrusted": False}
    assert report["expected_policy"]["blockedMarketplaces"] == [
        {"source": "github", "repo": "acme/untrusted-plugins"}
    ]
