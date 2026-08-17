from tools.posture.rules.insecure_transport import check_insecure_transport


def test_http_sse_endpoint_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"type": "sse", "url": "http://example.com/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-insecure-transport"
    assert findings[0].severity == "medium"
    assert findings[0].confidence == "high"
    assert "http://example.com/mcp" in findings[0].component_label


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
    assert "http://insecure.example/mcp" in findings[0].component_label


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
    components = {f.component_label for f in findings}
    assert any("a.example" in c for c in components)
    assert any("c.example" in c for c in components)


def test_flat_root_http_endpoint_flagged(tmp_path):
    """Flat `.mcp.json` maps (no mcpServers/servers wrapper) are checked."""
    manifest = {"playwright": {"url": "http://localhost:3000/mcp"}}
    findings = check_insecure_transport([(tmp_path / ".mcp.json", manifest)])
    assert len(findings) == 1
    assert "http://localhost:3000/mcp" in findings[0].component_label


def test_flat_root_https_not_flagged(tmp_path):
    manifest = {"playwright": {"url": "https://secure.example/mcp"}}
    findings = check_insecure_transport([(tmp_path / ".mcp.json", manifest)])
    assert findings == []


def test_disabled_server_not_flagged(tmp_path):
    """Servers with disabled: true are intentionally inactive and must not be flagged."""
    manifest = {
        "mcpServers": {
            "active": {"url": "http://active.example/mcp"},
            "inactive": {"url": "http://inactive.example/mcp", "disabled": True},
        }
    }
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert "active.example" in findings[0].component_label


def test_standards_block_uses_a02_2021(tmp_path):
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    s = findings[0].standards.to_dict()
    assert s["owasp_app_top_10"] == ["A02:2021"]
    assert s["owasp_agentic_top10"] == ["asi04"]
    assert s["owasp_mcp_top10"] == ["mcp04:2025"]
    # No CWE: don't force one.
    assert "cwe" not in s


def test_mcpservers_key_sets_claude_code_active_in(tmp_path):
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert findings[0].active_in == ["claude-code"]


def test_servers_key_leaves_active_in_empty(tmp_path):
    """VS Code `servers` key: host cannot be inferred, so active_in is empty."""
    manifest = {"servers": {"y": {"url": "http://y.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "mcp.json", manifest)])
    assert findings[0].active_in == []


def test_flat_root_leaves_active_in_empty(tmp_path):
    """Flat-root manifests have no host key, so active_in is empty."""
    manifest = {"playwright": {"url": "http://localhost:3000/mcp"}}
    findings = check_insecure_transport([(tmp_path / ".mcp.json", manifest)])
    assert findings[0].active_in == []


def test_cursor_mcp_json_sets_cursor_active_in(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(cursor_dir / "mcp.json", manifest)])
    assert findings[0].active_in == ["cursor"]


def test_claude_mcp_json_still_sets_claude_code_active_in(tmp_path):
    # Regression guard alongside the existing test_mcpservers_key_sets_
    # claude_code_active_in — same assertion, different path, to pin
    # that a *non*-.cursor path still resolves to claude-code.
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / "some/nested/mcp.json", manifest)])
    assert findings[0].active_in == ["claude-code"]


def test_cursor_cache_mcp_json_active_in_is_claude_code(tmp_path):
    # Boundary case: nested-under-.cursor/ but not the
    # exact .cursor/mcp.json shape — must resolve to claude-code, the
    # same as owning_host resolves it everywhere else.
    manifest = {"mcpServers": {"x": {"url": "http://x.example/mcp"}}}
    findings = check_insecure_transport([(tmp_path / ".cursor/cache/mcp.json", manifest)])
    assert findings[0].active_in == ["claude-code"]
