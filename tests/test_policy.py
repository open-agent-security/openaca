from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tools.component_ref import ComponentRef
from tools.policy import EndpointComponent, PolicyValidationError, apply_risk_gates, parse
from tools.policy_claude import compile_policy
from tools.policy_cli import main as policy_main


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
