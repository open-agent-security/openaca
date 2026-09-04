from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, Node
from tools.policy import (
    EndpointComponent,
    PluginTarget,
    PolicyEvaluationError,
    PolicyValidationError,
    apply_risk_gates,
    load,
    parse,
)
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


@pytest.mark.parametrize("field", [1, True])
def test_policy_rejects_non_string_fields_cleanly(field):
    with pytest.raises(PolicyValidationError, match="field names must be strings"):
        parse({"version": 1, "admission": {}, field: True})


def test_policy_requires_a_version_field():
    with pytest.raises(PolicyValidationError, match="policy.version must be 1"):
        parse(
            {
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {"default": "allowed"},
                    "skills": {"default": "allowed"},
                }
            }
        )


@pytest.mark.parametrize("section", ["mcps", "plugins", "skills"])
@pytest.mark.parametrize("default", [[], {}])
def test_policy_rejects_non_string_admission_defaults(section, default):
    admission = {
        "mcps": {"default": "allowed"},
        "plugins": {"default": "allowed"},
        "skills": {"default": "allowed"},
    }
    admission[section]["default"] = default

    with pytest.raises(PolicyValidationError, match="must be allowed or blocked"):
        parse({"version": 1, "admission": admission})


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_policy_requires_an_integer_version(version):
    with pytest.raises(PolicyValidationError, match="policy.version must be 1"):
        parse(
            {
                "version": version,
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {"default": "allowed"},
                    "skills": {"default": "allowed"},
                },
            }
        )


@pytest.mark.parametrize(
    ("suffix", "document"),
    [
        (
            "yaml",
            """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
risk_gates:
  vulnerabilities:
    ids: [CVE-2026-12345]
risk_gates: {}
""",
        ),
        (
            "json",
            '{"version": 1, "admission": {"mcps": {"default": "allowed"}, '
            '"plugins": {"default": "allowed"}, "skills": {"default": "allowed"}}, '
            '"risk_gates": {"vulnerabilities": {"ids": ["CVE-2026-12345"]}}, '
            '"risk_gates": {}}',
        ),
    ],
)
def test_policy_load_rejects_duplicate_mapping_keys(tmp_path, suffix, document):
    policy_path = tmp_path / f"policy.{suffix}"
    policy_path.write_text(document)

    with pytest.raises(PolicyValidationError, match="duplicate key"):
        load(policy_path)


def test_policy_load_rejects_an_unhashable_mapping_key(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("? [version]\n: 1\n")

    with pytest.raises(PolicyValidationError, match="unhashable key"):
        load(policy_path)


def test_policy_load_rejects_invalid_utf8(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_bytes(b"\xff\xfe")

    with pytest.raises(PolicyValidationError):
        load(policy_path)


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


def test_plugin_marketplace_targets_reject_normalized_overlap():
    with pytest.raises(PolicyValidationError, match="both allowed and blocked"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {
                        "default": "allowed",
                        "allowed": [{"marketplace": "https://github.com/acme/plugins"}],
                        "blocked": [{"marketplace": "https://github.com/acme/plugins.git"}],
                    },
                    "skills": {"default": "allowed"},
                },
            }
        )


def test_plugin_marketplace_targets_reject_renderer_equivalent_overlap():
    with pytest.raises(PolicyValidationError, match="both allowed and blocked"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {
                        "default": "allowed",
                        "allowed": [{"marketplace": "https://github.com/acme/plugins/"}],
                        "blocked": [{"marketplace": "https://github.com/acme/plugins"}],
                    },
                    "skills": {"default": "allowed"},
                },
            }
        )


@pytest.mark.parametrize(
    "marketplace",
    [
        "https://github.com/acme/plugins?ref=reviewed",
        "https://github.com/acme/plugins#reviewed",
    ],
)
def test_plugin_marketplace_targets_reject_urls_with_selectors(marketplace):
    with pytest.raises(PolicyValidationError, match="query or fragment"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {
                        "default": "allowed",
                        "allowed": [{"marketplace": marketplace}],
                    },
                    "skills": {"default": "allowed"},
                },
            }
        )


@pytest.mark.parametrize(
    "marketplace",
    [
        " ",
        "acme/plugins",
        "https://github.com:8443/acme/plugins",
        "https://github.com:notaport/acme/plugins",
        "https://git.example.com:notaport/acme/plugins",
    ],
)
def test_plugin_marketplace_targets_require_a_source_url(marketplace):
    with pytest.raises(PolicyValidationError, match="valid source URL"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {
                        "default": "allowed",
                        "blocked": [{"marketplace": marketplace}],
                    },
                    "skills": {"default": "allowed"},
                },
            }
        )


def test_plugin_marketplace_target_accepts_an_scp_style_git_url():
    parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {
                    "default": "allowed",
                    "allowed": [{"marketplace": "git@git.example.com:acme/plugins.git"}],
                },
                "skills": {"default": "allowed"},
            },
        }
    )


@pytest.mark.parametrize("url", [" ", "https://mcp.example.com:notaport", "https://${HOST}/mcp"])
def test_mcp_targets_require_a_parseable_url(url):
    with pytest.raises(PolicyValidationError, match="parseable URL"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {"default": "allowed", "blocked": [{"url": url}]},
                    "plugins": {"default": "allowed"},
                    "skills": {"default": "allowed"},
                },
            }
        )


@pytest.mark.parametrize("vulnerability_id", ["cve-2026-12345", "not-an-id", "  ", "GHSA-1234"])
def test_policy_rejects_a_malformed_vulnerability_gate_id(vulnerability_id):
    with pytest.raises(PolicyValidationError, match="malformed id"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {"default": "allowed"},
                    "skills": {"default": "allowed"},
                },
                "risk_gates": {"vulnerabilities": {"ids": [vulnerability_id]}},
            }
        )


def test_policy_rejects_an_unknown_posture_rule_id():
    with pytest.raises(PolicyValidationError, match="unknown rule id"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {"default": "allowed"},
                    "skills": {"default": "allowed"},
                },
                "risk_gates": {"posture": {"rules": ["openaca-posture-does-not-exist"]}},
            }
        )


@pytest.mark.parametrize("plugin", ["@scope/plugin", "foo@", "@marketplace"])
def test_plugin_target_rejects_identifiers_without_a_valid_marketplace_separator(plugin):
    with pytest.raises(PolicyValidationError, match="plugin@marketplace"):
        parse(
            {
                "version": 1,
                "admission": {
                    "mcps": {"default": "allowed"},
                    "plugins": {"default": "allowed", "blocked": [{"plugin": plugin}]},
                    "skills": {"default": "allowed"},
                },
            }
        )


def test_plugin_target_accepts_a_scoped_plugin_name():
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {
                    "default": "allowed",
                    "blocked": [{"plugin": "@scope/plugin@internal"}],
                },
                "skills": {"default": "allowed"},
            },
        }
    )

    target = policy.plugins.blocked[0]
    assert isinstance(target, PluginTarget)
    assert target.plugin == "@scope/plugin@internal"


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


def test_plugin_marketplace_target_matches_a_discovered_source_missing_the_git_suffix():
    """`_marketplace_source` (tools.parsers.claude_install) always appends
    `.git` for a GitHub-sourced marketplace, but a policy author writing the
    target by hand has no reason to include it. The match must normalize
    both sides rather than compare raw strings."""
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {
                    "default": "allowed",
                    "blocked": [{"marketplace": "https://github.com/acme/untrusted-plugins"}],
                },
                "skills": {"default": "allowed"},
            },
        }
    )
    plugin = ComponentRef(
        name="unsafe",
        extra={
            "component_type": "plugin",
            "marketplace": "untrusted",
            "marketplace_source": "https://github.com/acme/untrusted-plugins.git",
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


def test_risk_gate_on_plugin_child_blocks_the_owning_plugin_and_its_reported_child():
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
    assert decisions[0].controlled_by_plugin is False
    assert decisions[1].category == "mcps"
    assert decisions[1].blocked is True
    assert decisions[1].controlled_by_plugin is True
    assert decisions[1].subject.ref is plugin_copy
    assert decisions[1].risk_reasons == ("vulnerability GHSA-1234",)
    assert decisions[1].reasons == (
        "owning plugin: plugins default: allowed",
        "owning plugin: vulnerability GHSA-1234",
    )


def test_risk_gate_on_a_standalone_mcp_dependency_is_reported_not_enforceable():
    """A vulnerability on an agent-dependency package beneath a standalone MCP
    server (no owning plugin) has no host-native target of its own: it isn't
    the MCP server's own command/URL, and the spec defines containment
    resolution only for plugin ancestors ("If that occurrence belongs to a
    plugin, the owning plugin is always the target... If any step lacks an
    exact target, the result is not_enforceable"). The finding must surface as
    a not-enforceable limitation, not silently vanish as an unblocked "other"
    decision that compile_policy never renders."""
    policy = _policy(vulnerabilities={"ids": ["CVE-2026-12345"]})
    mcp = _mcp(["npx", "-y", "safe-mcp"])
    package = ComponentRef(name="left-pad", version="1.0.0")
    root = Node(key="target", kind="target", ref=None)
    mcp_node = Node(key="mcp", kind="mcp_server", ref=mcp)
    package_node = Node(key="package", kind="package", ref=package)
    graph = Graph(
        nodes={"target": root, "mcp": mcp_node, "package": package_node},
        edges=[Edge(parent="target", child="mcp"), Edge(parent="mcp", child="package")],
    )

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(mcp, graph), EndpointComponent(package, graph)],
        advisories=[{"id": "GHSA-1234", "aliases": ["CVE-2026-12345"]}],
        advisory_matches=[(package, "GHSA-1234")],
        posture_matches=[],
    )
    compilation = compile_policy(policy, decisions)

    assert decisions[0].category == "mcps"
    assert decisions[0].blocked is False
    assert decisions[1].category == "other"
    assert decisions[1].blocked is True
    assert any(
        "left-pad" in limitation and "not enforceable" in limitation
        for limitation in compilation.limitations
    )


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


def test_mcp_bundled_in_a_blocked_plugin_is_blocked_despite_an_exact_mcp_allow():
    """Spec: "A plugin remains the trust boundary for its bundled MCP
    servers, skills, and other contents." An MCP server contained by a
    plugin must inherit the plugin's own admission decision rather than
    being evaluated independently against `mcps.allowed`/`mcps.default`."""
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {
                    "default": "allowed",
                    "allowed": [{"command": ["npx", "-y", "safe-mcp"]}],
                },
                "plugins": {"default": "blocked"},
                "skills": {"default": "allowed"},
            },
        }
    )
    plugin = ComponentRef(
        name="bundle", extra={"component_type": "plugin", "marketplace": "internal"}
    )
    mcp = _mcp(["npx", "-y", "safe-mcp"])
    graph = _graph_with_plugin_child(plugin, mcp, "mcp_server")

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(plugin, graph), EndpointComponent(mcp, graph)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )

    assert decisions[0].blocked is True
    assert decisions[1].blocked is True
    assert decisions[1].reasons == ("owning plugin: plugins default: blocked",)


def test_mcp_bundled_in_an_allowed_plugin_is_not_blocked_by_mcps_default():
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "blocked"},
                "plugins": {"default": "allowed"},
                "skills": {"default": "allowed"},
            },
        }
    )
    plugin = ComponentRef(
        name="bundle", extra={"component_type": "plugin", "marketplace": "internal"}
    )
    mcp = _mcp(["npx", "-y", "bundled-mcp"])
    graph = _graph_with_plugin_child(plugin, mcp, "mcp_server")

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(plugin, graph), EndpointComponent(mcp, graph)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )
    compilation = compile_policy(policy, decisions)

    assert decisions[0].blocked is False
    assert decisions[1].blocked is False
    assert compilation.settings["allowManagedMcpServersOnly"] is True
    assert compilation.settings["allowedMcpServers"] == [
        {"serverCommand": ["npx", "-y", "bundled-mcp"]}
    ]


def test_plugin_admitted_mcp_conflicting_with_a_global_block_is_reported():
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {
                    "default": "blocked",
                    "blocked": [{"command": ["npx", "-y", "shared-mcp"]}],
                },
                "plugins": {"default": "allowed"},
                "skills": {"default": "allowed"},
            },
        }
    )
    plugin = ComponentRef(
        name="bundle", extra={"component_type": "plugin", "marketplace": "internal"}
    )
    mcp = _mcp(["npx", "-y", "shared-mcp"])
    graph = _graph_with_plugin_child(plugin, mcp, "mcp_server")

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(plugin, graph), EndpointComponent(mcp, graph)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )
    compilation = compile_policy(policy, decisions)

    assert decisions[1].blocked is False
    assert compilation.settings["deniedMcpServers"] == [
        {"serverCommand": ["npx", "-y", "shared-mcp"]}
    ]
    assert compilation.settings["allowedMcpServers"] == []
    assert any("conflicts with a global MCP block" in item for item in compilation.limitations)


def test_mcp_bundled_in_a_blocked_plugin_is_not_added_to_the_global_denylist():
    """A blocked plugin's bundled MCP already loses capability through that
    plugin's `enabledPlugins: false` entry. Compiling its command into the
    identity-agnostic `deniedMcpServers` list would also block an unrelated
    standalone server sharing the same command — this must not happen."""
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
    shared_command = ["npx", "-y", "shared-mcp"]
    bundled_mcp = _mcp(shared_command)
    graph = _graph_with_plugin_child(plugin, bundled_mcp, "mcp_server")
    standalone_mcp = _mcp(shared_command)

    decisions = apply_risk_gates(
        policy,
        [
            EndpointComponent(plugin, graph),
            EndpointComponent(bundled_mcp, graph),
            EndpointComponent(standalone_mcp),
        ],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )
    compilation = compile_policy(policy, decisions)

    assert decisions[1].blocked is True
    assert decisions[2].blocked is False
    assert compilation.settings["enabledPlugins"] == {"bundle@internal": False}
    assert "deniedMcpServers" not in compilation.settings


def test_package_bundled_in_a_blocked_plugin_inherits_the_plugin_decision():
    """Spec: a plugin remains the trust boundary for "bundled MCP servers,
    skills, and other contents" — including a component outside the
    mcps/plugins/skills taxonomy, such as a dependency package. It must
    reflect the real, plugin-enforced block rather than being reported as
    unconditionally outside policy scope, but since `enabledPlugins: false`
    already covers it, compile_policy must not also report it as an
    unenforceable risk block."""
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {
                    "default": "allowed",
                    "blocked": [{"plugin": "bundle@internal"}],
                },
                "skills": {"default": "allowed"},
            },
        }
    )
    plugin = ComponentRef(
        name="bundle", extra={"component_type": "plugin", "marketplace": "internal"}
    )
    package = ComponentRef(name="left-pad", version="1.0.0")
    graph = _graph_with_plugin_child(plugin, package, "package")

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(plugin, graph), EndpointComponent(package, graph)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[],
    )
    compilation = compile_policy(policy, decisions)

    assert decisions[1].category == "other"
    assert decisions[1].blocked is True
    assert decisions[1].reasons == ("owning plugin: admission blocked",)
    assert compilation.limitations == ()


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


def test_severity_gate_blocks_on_a_derivable_upstream_label():
    policy = _policy(vulnerabilities={"severity_at_least": "high"})
    mcp = _mcp(["npx", "-y", "safe-mcp"])

    decision = apply_risk_gates(
        policy,
        [EndpointComponent(mcp)],
        advisories=[{"id": "GHSA-1234", "database_specific": {"severity": "HIGH"}}],
        advisory_matches=[(mcp, "GHSA-1234")],
        posture_matches=[],
    )[0]

    assert decision.blocked is True


def test_severity_gate_fails_closed_on_an_advisory_with_no_derivable_severity():
    """An advisory with neither an upstream `database_specific.severity`
    label nor a parseable CVSS vector derives to severity "UNKNOWN".
    Treating that as "below threshold" would silently admit a component
    with a real, matched vulnerability finding just because the severity
    data happened to be missing — the same "not evidence that it is clean"
    principle the non-queryable-component check already enforces."""
    policy = _policy(vulnerabilities={"severity_at_least": "high"})
    mcp = _mcp(["npx", "-y", "safe-mcp"])

    with pytest.raises(PolicyEvaluationError, match="cannot evaluate severity_at_least gate"):
        apply_risk_gates(
            policy,
            [EndpointComponent(mcp)],
            advisories=[{"id": "GHSA-1234"}],
            advisory_matches=[(mcp, "GHSA-1234")],
            posture_matches=[],
        )


def test_standalone_skill_risk_block_is_reported_unenforceable_when_skills_default_allowed():
    policy = _policy(vulnerabilities={"ids": ["CVE-2026-12345"]})
    skill = ComponentRef(name="helper", extra={"component_type": "skill"})

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(skill)],
        advisories=[{"id": "GHSA-1234", "aliases": ["CVE-2026-12345"]}],
        advisory_matches=[(skill, "GHSA-1234")],
        posture_matches=[],
    )
    compilation = compile_policy(policy, decisions)

    assert decisions[0].blocked is True
    assert "strictPluginOnlyCustomization" not in compilation.settings
    assert any(
        "direct skill risk block is not enforceable" in limitation
        for limitation in compilation.limitations
    )


def test_standalone_skill_risk_block_is_not_reported_unenforceable_when_skills_default_blocked():
    """When `skills.default: blocked`, `strictPluginOnlyCustomization: ["skills"]`
    already blocks every standalone skill category-wide (see the "Standalone
    skill block" row in docs/specs/policy-compiler.md). Reporting an
    already-blocked skill's risk finding as "not enforceable" would falsely
    claim an enforcement gap that doesn't exist."""
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {"default": "allowed"},
                "skills": {"default": "blocked"},
            },
            "risk_gates": {"vulnerabilities": {"ids": ["CVE-2026-12345"]}},
        }
    )
    skill = ComponentRef(name="helper", extra={"component_type": "skill"})

    decisions = apply_risk_gates(
        policy,
        [EndpointComponent(skill)],
        advisories=[{"id": "GHSA-1234", "aliases": ["CVE-2026-12345"]}],
        advisory_matches=[(skill, "GHSA-1234")],
        posture_matches=[],
    )
    compilation = compile_policy(policy, decisions)

    assert decisions[0].blocked is True
    assert compilation.settings["strictPluginOnlyCustomization"] == ["skills"]
    assert not any("not enforceable" in limitation for limitation in compilation.limitations)


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


def test_claude_compiler_locks_down_marketplaces_without_marketplace_exceptions():
    policy = parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {
                    "default": "blocked",
                    "allowed": [{"plugin": "approved@internal"}],
                },
                "skills": {"default": "allowed"},
            },
        }
    )

    compilation = compile_policy(policy, [])

    assert compilation.settings["strictKnownMarketplaces"] == []
    assert "plugin default block is not enforceable" in compilation.limitations[0]
    assert "approved@internal" in compilation.limitations[1]


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


def test_policy_cli_rejects_a_nonexistent_project_directory(tmp_path):
    """Without `exists=True`, click's `Path` type silently skips existence
    checking (see click's own docs), so a mistyped `--project` would scan an
    empty tree instead of failing loudly — dropping every project-local
    component from the compiled policy with no error and no warning."""
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
"""
    )
    (tmp_path / "settings.json").write_text("{}")

    result = CliRunner().invoke(
        policy_main,
        [
            "compile",
            str(policy_path),
            "--target",
            str(tmp_path),
            "--project",
            str(tmp_path / "does-not-exist"),
            "--host",
            "claude",
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    assert "does not exist" in result.output


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
    install_path = tmp_path / "cache" / "unsafe" / "1.0"
    (install_path / ".claude-plugin").mkdir(parents=True)
    (install_path / ".claude-plugin" / "plugin.json").write_text('{"name": "unsafe"}')
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "unsafe@untrusted": [
                        {
                            "scope": "user",
                            "version": "1.0",
                            "installPath": str(install_path),
                        }
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


def test_policy_cli_rejects_an_invalid_discovered_marketplace_source(tmp_path):
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
                "extraKnownMarketplaces": {"untrusted": {"source": {"source": "git", "url": " "}}},
            }
        )
    )
    (tmp_path / "plugins").mkdir()
    install_path = tmp_path / "cache" / "unsafe" / "1.0"
    install_path.mkdir(parents=True)
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "unsafe@untrusted": [
                        {
                            "scope": "user",
                            "version": "1.0",
                            "installPath": str(install_path),
                        }
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
            "--managed-settings-dir",
            str(tmp_path / "managed"),
        ],
    )

    assert result.exit_code == 1
    assert "invalid marketplace source" in result.output
    assert "Traceback" not in result.output


def test_policy_cli_fails_when_an_enabled_plugin_is_missing_from_the_lockfile(tmp_path):
    """A plugin enabled in settings.json but absent from installed_plugins.json
    is a dropped inventory entry (tools.graph_build._seed_active_plugins), not
    just an ordinary "not found" component. Compilation must fail rather than
    silently evaluate admission/risk gates against an incomplete endpoint."""
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
"""
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"missing@nowhere": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {}}))

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
            "--managed-settings-dir",
            str(tmp_path / "managed"),
        ],
    )

    assert result.exit_code == 1
    assert "missing@nowhere" in result.output
    assert "installed_plugins.json" in result.output


def test_policy_cli_fails_when_an_enabled_plugin_install_path_is_unavailable(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
"""
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"missing@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "missing@m": [{"scope": "user", "version": "1.0", "installPath": "/missing"}]
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
            "--managed-settings-dir",
            str(tmp_path / "managed"),
        ],
    )

    assert result.exit_code == 1
    assert "missing@m" in result.output
    assert "installPath" in result.output


def test_policy_cli_fails_when_an_mcp_manifest_cannot_be_parsed(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
"""
    )
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / ".mcp.json").write_text("{not json")

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
            "--managed-settings-dir",
            str(tmp_path / "managed"),
        ],
    )

    assert result.exit_code == 1
    assert ".mcp.json" in result.output
    assert "could not parse" in result.output


def test_policy_cli_reports_an_endpoint_posture_finding_without_a_component_target(tmp_path):
    """`openaca-posture-api-endpoint-override` is an endpoint-level finding with
    no discovered component to attribute a block to (tools.posture._attach_bom_ref
    deliberately never assigns it a bom_ref). Gating on it must still surface the
    finding as not enforceable, per the compiler spec, instead of dropping it."""
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
risk_gates:
  posture:
    rules: ["openaca-posture-api-endpoint-override"]
"""
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://evil.example.com"}})
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
    assert any(
        "openaca-posture-api-endpoint-override" in limitation
        for limitation in report["limitations"]
    )


# --- `openaca policy compile`, one case per exception branch -----------------
#
# Captured before the compilation moved out of `tools/policy_cli.py` into
# `tools/policy_compile.py`, which retyped every `click` exception the moved
# bodies raise into `PolicyValidationError` / `PolicyEvaluationError`. These
# are the evidence that the command line did not move with it: the command
# already wraps both domain types back into a `click.ClickException`, which
# exits 1 and prints `Error: <message>`, so each stderr line and exit code
# below must be byte-identical before and after.

_ADMIT_ALL = """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
"""

_BLOCK_MCPS = """\
version: 1
admission:
  mcps:
    default: blocked
  plugins:
    default: allowed
  skills:
    default: allowed
"""

_VULN_GATE = (
    _ADMIT_ALL
    + """\
risk_gates:
  vulnerabilities:
    severity_at_least: high
"""
)


def _policy_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _compile(tmp_path: Path, policy_path: Path, *extra: str, target: Path | None = None):
    args = [
        "compile",
        str(policy_path),
        "--target",
        str(tmp_path if target is None else target),
        "--host",
        "claude",
        "--managed-settings-dir",
        str(tmp_path / "managed"),
        *extra,
    ]
    return CliRunner().invoke(policy_main, args)


def _skill(root: Path, name: str) -> None:
    directory = root / "skills" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def test_policy_compile_requires_output_unless_dry_run(tmp_path):
    """Exit code 2, not 1: the command performs this check itself before
    calling the compilation, so a CLI user keeps getting a `UsageError` even
    though the compilation now raises `PolicyValidationError` for the same
    argument combination."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL))

    assert result.exit_code == 2
    assert "Error: --output is required unless --dry-run is set" in result.output


def test_policy_compile_fails_when_no_agent_is_installed_at_the_target(tmp_path):
    target = tmp_path / "nowhere"

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL), "--dry-run", target=target)

    assert result.exit_code == 1
    assert result.output == f"Error: no installed agent found at {target}\n"


def test_policy_compile_fails_on_an_incomplete_inventory(tmp_path):
    """A malformed manifest under the target makes `build_agent_graph` warn, and
    an incomplete inventory would otherwise be evaluated as if the missing
    component did not exist. No `vulnerabilities` risk gate here, so the branch
    under test is the graph one."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL), "--dry-run")

    assert result.exit_code == 1
    assert result.output.startswith("Error: ")
    assert ".mcp.json" in result.output
    assert "could not parse" in result.output


def test_policy_compile_fails_on_a_non_queryable_component_under_a_vulnerability_gate(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    _skill(tmp_path, "deploy")

    result = _compile(tmp_path, _policy_file(tmp_path, _VULN_GATE), "--dry-run")

    assert result.exit_code == 1
    assert result.output == (
        "Error: vulnerability gates cannot evaluate non-queryable component(s): deploy\n"
    )


def test_the_non_queryable_message_truncates_past_three_components(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    for name in ("a", "b", "c", "d"):
        _skill(tmp_path, name)

    result = _compile(tmp_path, _policy_file(tmp_path, _VULN_GATE), "--dry-run")

    assert result.exit_code == 1
    assert result.output == (
        "Error: vulnerability gates cannot evaluate non-queryable component(s): a, b, c, ...\n"
    )


def test_policy_compile_fails_on_an_osv_load_warning(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"gh":{"command":"npx","args":["-y","@x/gh@1.0.0"]}}}', encoding="utf-8"
    )
    monkeypatch.setattr(
        "tools.policy_compile._load_osv_with_overlays",
        lambda refs: ([], ["osv.dev federation failed: boom"], 0, {}),
    )

    result = _compile(tmp_path, _policy_file(tmp_path, _VULN_GATE), "--dry-run")

    assert result.exit_code == 1
    assert result.output == "Error: osv.dev federation failed: boom\n"


def test_policy_compile_fails_when_the_managed_settings_directory_cannot_be_read(
    tmp_path, monkeypatch
):
    """Branch 1 of `_managed_key_collisions`: the directory `stat()` failing
    with an `OSError` that is not `FileNotFoundError`. A missing path is the
    early return, not a failure, so the error has to be induced."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    managed = tmp_path / "managed"
    managed.mkdir()
    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == managed:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL), "--dry-run")

    assert result.exit_code == 1
    assert result.output == (
        f"Error: cannot read managed settings directory {managed}: [Errno 13] Permission denied\n"
    )


def test_policy_compile_fails_when_the_dropin_path_cannot_be_read(tmp_path, monkeypatch):
    """Branch 3: the `managed-settings.d` drop-in `stat()` failing the same
    way."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    managed = tmp_path / "managed"
    dropins = managed / "managed-settings.d"
    dropins.mkdir(parents=True)
    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self == dropins:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL), "--dry-run")

    assert result.exit_code == 1
    assert result.output == (
        f"Error: cannot read managed settings drop-in path {dropins}: "
        "[Errno 13] Permission denied\n"
    )


def test_policy_compile_fails_when_the_dropin_path_is_not_a_directory(tmp_path):
    """Branch 4: the drop-in path exists but is not a directory."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    managed = tmp_path / "managed"
    managed.mkdir()
    dropins = managed / "managed-settings.d"
    dropins.write_text("not a directory", encoding="utf-8")

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL), "--dry-run")

    assert result.exit_code == 1
    assert result.output == (
        f"Error: managed settings drop-in path is not a directory: {dropins}\n"
    )


def test_policy_compile_fails_when_the_dropin_glob_cannot_be_read(tmp_path, monkeypatch):
    """Branch 5: `glob("*.json")` over the drop-in directory failing."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    dropins = tmp_path / "managed" / "managed-settings.d"
    dropins.mkdir(parents=True)
    real_glob = Path.glob

    def flaky_glob(self, pattern, *args, **kwargs):
        if self == dropins:
            raise PermissionError(13, "Permission denied")
        return real_glob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, "glob", flaky_glob)

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL), "--dry-run")

    assert result.exit_code == 1
    assert result.output == (
        f"Error: cannot read managed settings drop-in path {dropins}: "
        "[Errno 13] Permission denied\n"
    )


def test_policy_compile_fails_when_a_managed_settings_file_cannot_be_parsed(tmp_path):
    """Branch 6: a settings file that cannot be read, decoded or JSON-parsed."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    managed = tmp_path / "managed"
    managed.mkdir()
    settings = managed / "managed-settings.json"
    settings.write_text("{not json", encoding="utf-8")

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL), "--dry-run")

    assert result.exit_code == 1
    assert result.output.startswith(f"Error: cannot read managed settings file {settings}: ")


def test_policy_compile_fails_when_a_managed_settings_file_is_not_an_object(tmp_path):
    """Branch 7: a settings file whose top-level JSON is not an object."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    managed = tmp_path / "managed"
    managed.mkdir()
    settings = managed / "managed-settings.json"
    settings.write_text("[]", encoding="utf-8")

    result = _compile(tmp_path, _policy_file(tmp_path, _ADMIT_ALL), "--dry-run")

    assert result.exit_code == 1
    assert result.output == (
        f"Error: managed settings file {settings} must contain a JSON object\n"
    )


def test_policy_compile_text_report_and_written_artifact(tmp_path):
    """The text report, the artifact on disk and the digest over it. The
    `--project`-omitted note is the command's own, printed to stderr after the
    report — a programmatic caller wanting it prints its own."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"safe":{"command":"npx","args":["-y","safe-mcp"]}}}', encoding="utf-8"
    )
    output = tmp_path / "artifact.json"

    result = _compile(tmp_path, _policy_file(tmp_path, _BLOCK_MCPS), "--output", str(output))

    assert result.exit_code == 0, result.output
    assert result.output == (
        "Expected Claude policy:\n"
        "{\n"
        '  "allowManagedMcpServersOnly": true,\n'
        '  "allowedMcpServers": [],\n'
        '  "deniedMcpServers": [\n'
        "    {\n"
        '      "serverCommand": [\n'
        '        "npx",\n'
        '        "-y",\n'
        '        "safe-mcp"\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "\n"
        "Components: 1\n"
        "  blocked: mcp-server/npm/safe-mcp (mcps default: blocked)\n"
        "note: project-local configuration was not scanned; pass --project to include it\n"
    )
    artifact = output.read_text(encoding="utf-8")
    assert json.loads(artifact)["allowManagedMcpServersOnly"] is True
    assert artifact.endswith("\n")


def test_policy_compile_json_report_digest_matches_the_written_artifact(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "artifact.json"

    result = _compile(
        tmp_path,
        _policy_file(tmp_path, _BLOCK_MCPS),
        "--output",
        str(output),
        "--format",
        "json",
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout.splitlines()[0])
    assert report["artifact"]["written"] is True
    assert report["artifact"]["output"] == str(output)
    assert report["artifact"]["digest"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_policy_compile_lets_an_artifact_write_oserror_escape(tmp_path):
    """The one failure the domain errors deliberately do not cover.
    `_write_artifact` translates none of `mkdir` / the temp write /
    `Path.replace`'s `OSError`s, so it escapes the command uncaught. Wrapping it
    in a domain error the command catches would turn a traceback into
    `Error: ...` with exit code 1, which is a command-line behaviour change."""
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    os.chmod(readonly, 0o500)
    try:
        result = _compile(
            tmp_path,
            _policy_file(tmp_path, _BLOCK_MCPS),
            "--output",
            str(readonly / "artifact.json"),
        )
    finally:
        os.chmod(readonly, 0o700)

    assert isinstance(result.exception, OSError)
    assert not isinstance(result.exception, click.ClickException)
