import pytest

from tools.fleet.redaction import RedactionError, validate_fleet_upload_payload


def test_rejects_absolute_home_paths_in_top_level_metadata():
    payload = _payload(target_locator="/Users/alex/.claude")

    with pytest.raises(RedactionError) as exc:
        validate_fleet_upload_payload(payload)

    assert "target_locator" in str(exc.value)
    assert "/Users/alex" not in str(exc.value)


def test_rejects_absolute_home_paths_in_bom_openaca_properties():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:source_manifest",
                            "value": "/home/alex/.claude/settings.json",
                        }
                    ]
                }
            ]
        }
    )

    with pytest.raises(RedactionError) as exc:
        validate_fleet_upload_payload(payload)

    assert "bom.components[0].properties[0].value" in str(exc.value)
    assert "/home/alex" not in str(exc.value)


def test_does_not_reject_pass_through_cyclonedx_external_reference_paths():
    payload = _payload(
        bom={
            "components": [
                {
                    "externalReferences": [
                        {"type": "vcs", "url": "https://github.com/example/repo"}
                    ]
                }
            ]
        }
    )

    validate_fleet_upload_payload(payload)


def test_rejects_full_urls_with_query_strings():
    payload = _payload(
        posture_findings=[
            {
                "rule_id": "openaca-posture-insecure-transport",
                "rule_version": "1",
                "severity": "MEDIUM",
                "scope": "component",
                "component_identity": "mcp-server/test",
                "evidence": {"transport": "http://example.test/mcp?token=secret"},
            }
        ]
    )

    with pytest.raises(RedactionError) as exc:
        validate_fleet_upload_payload(payload)

    assert "posture_findings[0].evidence.transport" in str(exc.value)
    assert "secret" not in str(exc.value)


def test_rejects_token_looking_values():
    payload = _payload(
        posture_findings=[
            {
                "rule_id": "openaca-posture-mutable-install-reference",
                "rule_version": "1",
                "severity": "LOW",
                "scope": "component",
                "component_identity": "mcp-server/test",
                "evidence": {"install_ref": "ghp_1234567890abcdefghijklmnopqrstuv"},
            }
        ]
    )

    with pytest.raises(RedactionError):
        validate_fleet_upload_payload(payload)


def test_allows_relative_manifest_paths_and_endpoint_locator():
    payload = _payload(
        target_locator="endpoint:user-scope",
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:source_manifest", "value": ".claude/settings.json"},
                        {"name": "openaca:pinned", "value": "false"},
                    ]
                }
            ]
        },
        posture_findings=[
            {
                "rule_id": "openaca-posture-insecure-transport",
                "rule_version": "1",
                "severity": "MEDIUM",
                "scope": "component",
                "component_identity": "mcp-server/test",
                "evidence": {"transport": "http", "manifest_path": ".mcp.json"},
            }
        ],
    )

    validate_fleet_upload_payload(payload)


def test_rejects_unapproved_posture_evidence_keys():
    payload = _payload(
        posture_findings=[
            {
                "rule_id": "openaca-posture-mcp-auto-approve",
                "rule_version": "1",
                "severity": "MEDIUM",
                "scope": "bom",
                "component_identity": None,
                "evidence": {"approved_tool_names": ["shell"]},
            }
        ]
    )

    with pytest.raises(RedactionError) as exc:
        validate_fleet_upload_payload(payload)

    assert "approved_tool_names" in str(exc.value)


def _payload(**overrides):
    payload = {
        "asset_id": "asset-123",
        "source": "endpoint",
        "openaca_version": "0.1.0b5",
        "target_locator": "endpoint:user-scope",
        "content_hash": "sha256:abc",
        "bom": {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []},
        "posture_findings": [],
    }
    payload.update(overrides)
    return payload
