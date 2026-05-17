from tools.posture.rules.missing_auth import check_missing_auth


def test_remote_no_auth_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"type": "sse", "url": "https://example.com/mcp"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-missing-remote-auth"
    assert findings[0].severity == "low"
    assert findings[0].confidence == "medium"
    assert "https://example.com/mcp" in findings[0].component_label


def test_remote_with_auth_header_not_flagged(tmp_path):
    manifest = {
        "mcpServers": {
            "x": {
                "type": "sse",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer ${ENV_TOKEN}"},
            }
        }
    }
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_remote_with_lowercase_authorization_header_not_flagged(tmp_path):
    manifest = {
        "mcpServers": {
            "x": {
                "url": "https://example.com/mcp",
                "headers": {"authorization": "Bearer xyz"},
            }
        }
    }
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_remote_with_env_token_field_not_flagged(tmp_path):
    manifest = {
        "mcpServers": {
            "x": {"type": "sse", "url": "https://example.com/mcp", "env": {"TOKEN": "..."}}
        }
    }
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_remote_with_token_field_not_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"url": "https://example.com/mcp", "token": "abc"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_remote_with_apikey_field_not_flagged(tmp_path):
    manifest = {"mcpServers": {"x": {"url": "https://example.com/mcp", "apiKey": "abc"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_stdio_not_in_scope(tmp_path):
    """Stdio MCPs have no URL — out of scope for this rule."""
    manifest = {"mcpServers": {"x": {"command": "uvx", "args": ["mcp-x"]}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings == []


def test_servers_envelope_also_walked(tmp_path):
    manifest = {"servers": {"y": {"url": "https://example.com/mcp"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert "https://example.com/mcp" in findings[0].component_label


def test_flat_root_no_auth_flagged(tmp_path):
    """Flat `.mcp.json` maps (no mcpServers/servers wrapper) are checked."""
    manifest = {"playwright": {"url": "https://example.com/mcp"}}
    findings = check_missing_auth([(tmp_path / ".mcp.json", manifest)])
    assert len(findings) == 1
    assert "https://example.com/mcp" in findings[0].component_label


def test_flat_root_with_auth_not_flagged(tmp_path):
    manifest = {
        "playwright": {
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer token"},
        }
    }
    findings = check_missing_auth([(tmp_path / ".mcp.json", manifest)])
    assert findings == []


def test_disabled_server_not_flagged(tmp_path):
    """Servers with disabled: true are intentionally inactive and must not be flagged."""
    manifest = {
        "mcpServers": {
            "active": {"url": "https://active.example/mcp"},
            "inactive": {"url": "https://inactive.example/mcp", "disabled": True},
        }
    }
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert "active.example" in findings[0].component_label


def test_standards_block(tmp_path):
    manifest = {"mcpServers": {"x": {"url": "https://example.com/mcp"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    s = findings[0].standards.to_dict()
    assert s["owasp_app_top_10"] == ["A01:2021", "A07:2021"]
    assert s["owasp_agentic_top10"] == ["asi03"]
    assert s["owasp_mcp_top10"] == ["mcp07:2025"]


def test_mcpservers_key_sets_claude_code_active_in(tmp_path):
    manifest = {"mcpServers": {"x": {"url": "https://example.com/mcp"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings[0].active_in == ["claude-code"]


def test_servers_key_leaves_active_in_empty(tmp_path):
    """VS Code `servers` key: host cannot be inferred, so active_in is empty."""
    manifest = {"servers": {"y": {"url": "https://example.com/mcp"}}}
    findings = check_missing_auth([(tmp_path / "mcp.json", manifest)])
    assert findings[0].active_in == []


def test_flat_root_leaves_active_in_empty(tmp_path):
    """Flat-root manifests have no host key, so active_in is empty."""
    manifest = {"playwright": {"url": "https://example.com/mcp"}}
    findings = check_missing_auth([(tmp_path / ".mcp.json", manifest)])
    assert findings[0].active_in == []
