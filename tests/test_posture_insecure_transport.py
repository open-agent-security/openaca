from tools.posture.rules.insecure_transport import check_insecure_transport


def test_http_sse_endpoint_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"type": "sse", "url": "http://example.com/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-insecure-transport"
    assert findings[0].severity == "medium"
    assert findings[0].confidence == "high"
    assert "http://example.com/mcp" in findings[0].component


def test_https_sse_endpoint_not_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"type": "sse", "url": "https://example.com/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_stdio_command_not_flagged(tmp_path):
    """Stdio MCPs have no URL — out of scope for transport check."""
    manifest = {"mcpServers": {"x": {"command": "uvx mcp-x"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_servers_envelope_also_walked(tmp_path):
    """VS Code uses `servers` instead of `mcpServers`."""
    manifest = {"servers": {"y": {"url": "http://insecure.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert "http://insecure.example/mcp" in findings[0].component


def test_multiple_endpoints_each_emit_finding(tmp_path):
    manifest = {
        "mcpServers": {
            "a": {"url": "http://a.example/mcp"},
            "b": {"url": "https://b.example/mcp"},
            "c": {"url": "http://c.example/mcp"},
        }
    }
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 2
    components = {f.component for f in findings}
    assert any("a.example" in c for c in components)
    assert any("c.example" in c for c in components)


def test_standards_block_uses_a02_2021(tmp_path):
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    s = findings[0].standards.to_dict()
    assert s["owasp_app_top_10"] == ["A02:2021"]
    assert s["owasp_agentic_top10"] == ["asi04"]
    assert s["owasp_mcp_top10"] == ["mcp04:2025"]
    # No CWE: don't force one.
    assert "cwe" not in s
