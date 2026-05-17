import json

from tools.component_ref import ComponentRef
from tools.parsers.claude_plugin import parse as parse_plugin
from tools.parsers.mcp_json import parse as parse_mcp
from tools.posture.rules.mutable_install import check_mutable_install


def test_mcp_unpinned_uvx_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "uvx", "args": ["mcp-bar"]}}})
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-mutable-install-reference"
    assert findings[0].severity == "low"
    assert findings[0].confidence == "high"
    assert "uvx mcp-bar" in findings[0].component_label
    assert findings[0].standards.cwe == ["CWE-1357"]


def test_mcp_pinned_uvx_not_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "uvx", "args": ["mcp-bar==1.0.0"]}}})
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert findings == []


def test_mcp_unpinned_npx_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"x": {"command": "npx", "args": ["@modelcontextprotocol/server-foo"]}}}
        )
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert len(findings) == 1
    assert "npx @modelcontextprotocol/server-foo" in findings[0].component_label
    # Name contains "mcp"-adjacent token; ensure MCP taxonomy code is added.
    assert findings[0].standards.owasp_mcp_top10 == ["mcp04:2025"]


def test_mcp_pinned_npx_not_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "x": {"command": "npx", "args": ["@modelcontextprotocol/server-foo@1.2.3"]}
                }
            }
        )
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert findings == []


def test_mcp_local_binary_not_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "./local-server"}}})
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert findings == []


def test_mcp_npx_at_latest_flagged(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "npx", "args": ["mcp-server-foo@latest"]}}})
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    assert len(findings) == 1
    assert "@latest" in findings[0].component_label


def test_mutable_install_emits_standards_block_with_cwe_scorecard_slsa(tmp_path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"command": "uvx", "args": ["mcp-bar"]}}})
    )
    refs = parse_mcp(tmp_path / "mcp.json")
    findings = check_mutable_install(refs)
    s = findings[0].standards.to_dict()
    assert s["cwe"] == ["CWE-1357"]
    assert s["openssf_scorecard"] == ["Pinned-Dependencies"]
    assert s["slsa"] == ["immutable-references"]
    assert s["owasp_agentic_top10"] == ["asi04"]


def test_unversioned_plugin_without_commit_sha_is_flagged():
    ref = ComponentRef(
        ecosystem="claude-plugin",
        name="feature-dev",
        version="unknown",
        component_identity="claude-plugin/feature-dev@unknown",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.feature-dev@official[0]",
        extra={
            "component_type": "plugin",
            "runtime_hosts": ["claude-code"],
            "declared_by": {"kind": "skill_lock", "path": "installed_plugins.json"},
            "component_path": [{"type": "plugin", "name": "feature-dev"}],
            "gitCommitSha": None,
        },
    )

    findings = check_mutable_install([ref])

    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-mutable-install-reference"
    assert findings[0].component_label == "claude-plugin/feature-dev@unknown"
    assert findings[0].active_in == ["claude-code"]


def test_unversioned_plugin_with_commit_sha_is_not_flagged():
    ref = ComponentRef(
        ecosystem="claude-plugin",
        name="reboot-chat-app",
        version="unknown",
        component_identity="claude-plugin/reboot-chat-app@unknown",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.reboot-chat-app@local[0]",
        extra={
            "component_type": "plugin",
            "runtime_hosts": ["claude-code"],
            "component_path": [{"type": "plugin", "name": "reboot-chat-app"}],
            "gitCommitSha": "79a2f53263ba0123456789abcdef0123456789ab",
        },
    )

    findings = check_mutable_install([ref])

    assert findings == []


def test_repo_scan_plugin_without_version_not_flagged(tmp_path):
    """Repo-mode refs from claude_plugin.parse() must not produce false positives.

    parse() emits an empty extra dict (no "gitCommitSha" key), so a plugin
    manifest that omits version must not be reported as a mutable install ref.
    """
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": "my-plugin"}))

    refs = parse_plugin(plugin_dir / "plugin.json")
    findings = check_mutable_install(refs)

    assert findings == []
