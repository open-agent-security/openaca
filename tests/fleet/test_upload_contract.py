import pytest

from tools.fleet.upload_contract import FleetUploadContractError, enforce_fleet_upload_contract


def test_allows_endpoint_inventory_paths_and_benign_url_queries():
    payload = _payload(
        target_locator="endpoint:user-scope",
        bom={
            "components": [
                {
                    "name": "mcp-server/test",
                    "properties": [
                        {
                            "name": "openaca:source_manifest",
                            "value": "/Users/alex/.claude/settings.json",
                        },
                        {
                            "name": "openaca:source_provenance",
                            "value": "https://example.test/package?version=1.2.3",
                        },
                    ],
                }
            ]
        },
    )

    enforce_fleet_upload_contract(payload)


def test_rejects_token_like_values_without_echoing_value():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:source_provenance",
                            "value": "ghp_1234567890abcdefghijklmnopqrstuv",
                        }
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[0].value" in str(exc.value)
    assert "ghp_" not in str(exc.value)


def test_rejects_secret_query_parameters_without_rejecting_all_queries():
    payload = _payload(
        bom={
            "components": [
                {
                    "externalReferences": [
                        {"type": "distribution", "url": "https://example.test/mcp?token=secret"}
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].externalReferences[0].url" in str(exc.value)
    assert "secret" not in str(exc.value)


def test_rejects_forbidden_property_names():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:env", "value": "PATH"},
                        {"name": "openaca:command", "value": "npx"},
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[0].value" in str(exc.value)
    assert "PATH" not in str(exc.value)


def test_rejects_raw_config_body_property_names():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:raw_config", "value": '{"mcpServers": {}}'},
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[0].value" in str(exc.value)
    assert "mcpServers" not in str(exc.value)


def test_rejects_full_shell_argv_properties():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:command", "value": "npx"},
                        {"name": "openaca:command_args", "value": "--token abc @example/mcp"},
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "--token" not in str(exc.value)


def test_rejects_binary_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:identity", "value": "mcp-stdio/binary:python"},
                        {
                            "name": "openaca:install_source",
                            "value": "python server.py --tenant alice",
                        },
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "tenant" not in str(exc.value)


def test_rejects_local_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:identity", "value": "mcp-stdio/local:discord"},
                        {
                            "name": "openaca:install_source",
                            "value": "bun run --cwd ${CLAUDE_PLUGIN_ROOT} start",
                        },
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "CLAUDE_PLUGIN_ROOT" not in str(exc.value)


def test_rejects_npx_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:identity",
                            "value": "mcp-stdio/npx-unpinned:@example/mcp",
                        },
                        {
                            "name": "openaca:install_source",
                            "value": "npx @example/mcp --token abc",
                        },
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "token" not in str(exc.value)


def test_rejects_uvx_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:identity",
                            "value": "mcp-stdio/uvx-unpinned:mcp-server",
                        },
                        {
                            "name": "openaca:install_source",
                            "value": "uvx mcp-server --api-key secret",
                        },
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "secret" not in str(exc.value)


def test_rejects_pinned_npm_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "purl": "pkg:npm/%40scope%2Fpkg@1.2.3",
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {
                            "name": "openaca:install_source",
                            "value": "npx @scope/pkg@1.2.3 --token abc",
                        },
                    ],
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "token" not in str(exc.value)


def test_rejects_pinned_pypi_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "purl": "pkg:pypi/mcp-server@1.2.3",
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {
                            "name": "openaca:install_source",
                            "value": "uvx mcp-server==1.2.3 --api-key secret",
                        },
                    ],
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "secret" not in str(exc.value)


def test_rejects_pinned_docker_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "purl": "pkg:docker/hashicorp/terraform-mcp-server@0.4.0",
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {
                            "name": "openaca:install_source",
                            "value": (
                                "docker run -e TFE_TOKEN=${TFE_TOKEN} "
                                "hashicorp/terraform-mcp-server:0.4.0"
                            ),
                        },
                    ],
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "TFE_TOKEN" not in str(exc.value)


def test_allows_pinned_mcp_clean_install_source():
    payload = _payload(
        bom={
            "components": [
                {
                    "purl": "pkg:npm/%40scope%2Fpkg@1.2.3",
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {"name": "openaca:install_source", "value": "npx @scope/pkg@1.2.3"},
                    ],
                }
            ]
        }
    )

    enforce_fleet_upload_contract(payload)


def test_allows_package_install_source_references():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:identity",
                            "value": "mcp-stdio/npx-unpinned:@example/mcp",
                        },
                        {"name": "openaca:install_source", "value": "npx @example/mcp"},
                    ]
                }
            ]
        }
    )

    enforce_fleet_upload_contract(payload)


def test_rejects_adr0029_unpinned_npx_mcp_install_source_with_raw_argv():
    # ADR-0029: unpinned package MCPs carry mcp-server/<name> identity (no PURL).
    # The contract must enforce the 2-token limit even for this new identity shape.
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {"name": "openaca:identity", "value": "mcp-server/my-mcp"},
                        {
                            "name": "openaca:install_source",
                            "value": "npx -y @scope/pkg --token sk-1234",
                        },
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[2].value" in str(exc.value)
    assert "sk-1234" not in str(exc.value)


def test_allows_adr0029_unpinned_npx_mcp_clean_install_source():
    # After _prepare_fleet_component trims the argv, the contract must accept the result.
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {"name": "openaca:identity", "value": "mcp-server/my-mcp"},
                        {"name": "openaca:install_source", "value": "npx @scope/pkg"},
                    ]
                }
            ]
        }
    )

    enforce_fleet_upload_contract(payload)


def test_rejects_adr0029_unpinned_uv_tool_run_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {"name": "openaca:identity", "value": "mcp-server/weather"},
                        {
                            "name": "openaca:source_identity",
                            "value": "mcp-stdio/uvx-unpinned:weather-mcp",
                        },
                        {
                            "name": "openaca:install_source",
                            "value": "uv tool run weather-mcp --token sk-1234",
                        },
                    ]
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[3].value" in str(exc.value)
    assert "sk-1234" not in str(exc.value)


def test_allows_adr0029_unpinned_uv_tool_run_mcp_clean_install_source():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {"name": "openaca:identity", "value": "mcp-server/weather"},
                        {
                            "name": "openaca:source_identity",
                            "value": "mcp-stdio/uvx-unpinned:weather-mcp",
                        },
                        {"name": "openaca:install_source", "value": "uvx weather-mcp"},
                    ]
                }
            ]
        }
    )

    enforce_fleet_upload_contract(payload)


def test_rejects_adr0029_pinned_mcp_install_source_with_raw_argv():
    # ADR-0029: pinned package MCPs carry mcp-server/<name> identity plus a PURL.
    # The contract must enforce the 2-token limit for this identity shape too.
    payload = _payload(
        bom={
            "components": [
                {
                    "purl": "pkg:npm/%40scope%2Fpkg@1.2.3",
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {"name": "openaca:identity", "value": "mcp-server/my-mcp"},
                        {
                            "name": "openaca:install_source",
                            "value": "npx @scope/pkg@1.2.3 --token secret",
                        },
                    ],
                }
            ]
        }
    )

    with pytest.raises(FleetUploadContractError) as exc:
        enforce_fleet_upload_contract(payload)

    assert "bom.components[0].properties[2].value" in str(exc.value)
    assert "secret" not in str(exc.value)


def test_allows_posture_evidence_without_rule_specific_allowlist():
    payload = _payload(
        posture_findings=[
            {
                "rule_id": "openaca-posture-mcp-auto-approve",
                "rule_version": "1",
                "severity": "MEDIUM",
                "scope": "bom",
                "component_identity": None,
                "summary": "Auto-approve is enabled",
                "fix": "Disable auto-approve.",
                "evidence": {
                    "approved_tool_names": ["Read", "Write"],
                    "manifest_path": "/Users/alex/.claude/settings.json",
                },
            }
        ]
    )

    enforce_fleet_upload_contract(payload)


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
