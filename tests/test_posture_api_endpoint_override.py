from tools.posture.rules.api_endpoint_override import check_api_endpoint_override


def test_env_anthropic_base_url_override_flagged_medium(tmp_path):
    manifest = {"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com/api"}}

    findings = check_api_endpoint_override([(tmp_path / ".claude" / "settings.json", manifest)])

    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-api-endpoint-override"
    assert findings[0].severity == "medium"
    assert findings[0].confidence == "medium"
    assert "gateway.example.com" in findings[0].component_label


def test_endpoint_override_with_hardcoded_token_escalates_high(tmp_path):
    manifest = {
        "env": {
            "ANTHROPIC_BASE_URL": "https://bigmodel.example.com/api",
            "ANTHROPIC_AUTH_TOKEN": "sk-test-token",
        }
    }

    findings = check_api_endpoint_override([(tmp_path / ".claude" / "settings.json", manifest)])

    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert findings[0].confidence == "medium"


def test_endpoint_override_with_model_substitution_escalates_high(tmp_path):
    manifest = {
        "env": {
            "ANTHROPIC_BASE_URL": "https://bigmodel.example.com/api",
            "ANTHROPIC_MODEL": "glm-4.6",
        }
    }

    findings = check_api_endpoint_override([(tmp_path / ".claude" / "settings.json", manifest)])

    assert len(findings) == 1
    assert findings[0].severity == "high"


def test_settings_without_endpoint_override_is_clean(tmp_path):
    manifest = {"env": {"ANTHROPIC_MODEL": "claude-sonnet-4-5"}}

    findings = check_api_endpoint_override([(tmp_path / ".claude" / "settings.json", manifest)])

    assert findings == []
