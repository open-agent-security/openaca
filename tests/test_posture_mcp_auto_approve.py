from tools.posture.rules.mcp_auto_approve import check_mcp_auto_approve


def test_mcp_autoapprove_true_flagged(tmp_path):
    manifest = {
        "mcpServers": {
            "unsafe": {
                "url": "https://example.com/mcp",
                "autoApprove": True,
            }
        }
    }

    findings = check_mcp_auto_approve([(tmp_path / ".mcp.json", manifest)])

    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-mcp-auto-approve"
    assert findings[0].severity == "medium"
    assert findings[0].confidence == "medium"
    assert "unsafe" in findings[0].component_label


def test_mcp_autoapprove_non_empty_tool_list_flagged(tmp_path):
    manifest = {"mcpServers": {"unsafe": {"url": "https://example.com/mcp", "autoApprove": ["*"]}}}

    findings = check_mcp_auto_approve([(tmp_path / ".mcp.json", manifest)])

    assert len(findings) == 1


def test_mcp_autoapprove_false_or_empty_list_is_clean(tmp_path):
    manifest = {
        "mcpServers": {
            "false": {"url": "https://example.com/mcp", "autoApprove": False},
            "empty": {"url": "https://example.com/other", "autoApprove": []},
        }
    }

    findings = check_mcp_auto_approve([(tmp_path / ".mcp.json", manifest)])

    assert findings == []


def test_disabled_mcp_server_autoapprove_is_clean(tmp_path):
    manifest = {
        "mcpServers": {
            "disabled": {
                "url": "https://example.com/mcp",
                "autoApprove": True,
                "disabled": True,
            }
        }
    }

    findings = check_mcp_auto_approve([(tmp_path / ".mcp.json", manifest)])

    assert findings == []


def test_settings_file_mcp_autoapprove_flagged(tmp_path):
    """autoApprove in settings.json mcpServers must be caught by the rule."""
    settings_manifest = {
        "mcpServers": {
            "inline-server": {
                "command": "npx",
                "args": ["-y", "some-mcp@1.0.0"],
                "autoApprove": ["read_file", "list_dir"],
            }
        },
        "env": {},
    }
    settings_path = tmp_path / "settings.json"

    findings = check_mcp_auto_approve([(settings_path, settings_manifest)])

    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-mcp-auto-approve"
    assert "inline-server" in findings[0].component_label


def test_cursor_mcp_json_not_flagged(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    manifest = {"mcpServers": {"x": {"autoApprove": True}}}
    findings = check_mcp_auto_approve([(cursor_dir / "mcp.json", manifest)])
    assert findings == []


def test_claude_mcp_json_still_flagged(tmp_path):
    # Regression guard: the Claude-only case must keep working.
    manifest = {"mcpServers": {"x": {"autoApprove": True}}}
    findings = check_mcp_auto_approve([(tmp_path / "mcp.json", manifest)])
    assert len(findings) == 1
    assert findings[0].active_in == ["claude-code"]
