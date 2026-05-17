import json

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
