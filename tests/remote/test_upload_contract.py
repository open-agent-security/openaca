import pytest

from tools.remote.upload_contract import RemoteUploadContractError, enforce_remote_upload_contract


def test_allows_relative_inventory_paths_and_bare_host_urls():
    """Relative inventory paths and bare-host URLs are allowed. Absolute
    paths and URLs with paths/queries in `openaca:*` property values are
    rejected — the collector's `_redact_payload_for_remote` is responsible
    for normalizing them BEFORE this enforcer runs.
    """
    payload = _payload(
        target_locator="endpoint:user-scope",
        bom={
            "components": [
                {
                    "name": "mcp-server/test",
                    "properties": [
                        {
                            "name": "openaca:source_manifest",
                            "value": "settings.json",
                        },
                        {
                            "name": "openaca:source_provenance",
                            "value": "https://example.test",
                        },
                    ],
                }
            ]
        },
    )

    enforce_remote_upload_contract(payload)


def test_rejects_url_query_in_openaca_property():
    """The backend rejects URL queries to prevent leaking parameters; the
    CLI now mirrors that. Bare-host URLs are still allowed (see the test
    above).
    """
    import pytest

    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:source_provenance",
                            "value": "https://example.test/package?version=1.2.3",
                        }
                    ]
                }
            ]
        }
    )
    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)
    assert "URL with a path" in str(exc.value)


def test_rejects_bare_userinfo_url_in_openaca_property():
    """A URL with credentials in userinfo but no path/query
    (`https://user:pass@host`) must be rejected. `_redact_url_for_remote`
    strips userinfo, but a stale offline-cache payload replayed via
    `_replay_pending_uploads` is only validated by this contract (no
    redaction pass), so the contract is the last line of defense against
    uploading credentials.
    """
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:install_source",
                            "value": "https://alice:s3cr3t@example.com",
                        }
                    ]
                }
            ]
        }
    )
    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)
    assert "credentials" in str(exc.value)


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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "bom.components[0].properties[1].value" in str(exc.value)
    assert "CLAUDE_PLUGIN_ROOT" not in str(exc.value)


def test_rejects_npx_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:component_type",
                            "value": "mcp_server",
                        },
                        {
                            "name": "openaca:identity",
                            "value": "mcp-server/example",
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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "bom.components[0].properties[2].value" in str(exc.value)
    assert "token" not in str(exc.value)


def test_rejects_uvx_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:component_type",
                            "value": "mcp_server",
                        },
                        {
                            "name": "openaca:identity",
                            "value": "mcp-server/example",
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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "bom.components[0].properties[2].value" in str(exc.value)
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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

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

    enforce_remote_upload_contract(payload)


def test_allows_package_install_source_references():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {
                            "name": "openaca:component_type",
                            "value": "mcp_server",
                        },
                        {
                            "name": "openaca:identity",
                            "value": "mcp-server/example",
                        },
                        {"name": "openaca:install_source", "value": "npx @example/mcp"},
                    ]
                }
            ]
        }
    )

    enforce_remote_upload_contract(payload)


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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "bom.components[0].properties[2].value" in str(exc.value)
    assert "sk-1234" not in str(exc.value)


def test_allows_adr0029_unpinned_npx_mcp_clean_install_source():
    # After _prepare_remote_component trims the argv, the contract must accept the result.
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

    enforce_remote_upload_contract(payload)


def test_rejects_adr0029_unpinned_uv_tool_run_mcp_install_source_with_raw_argv():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {"name": "openaca:identity", "value": "mcp-server/weather"},
                        {
                            "name": "openaca:install_source",
                            "value": "uv tool run weather-mcp --token sk-1234",
                        },
                    ]
                }
            ]
        }
    )

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "bom.components[0].properties[2].value" in str(exc.value)
    assert "sk-1234" not in str(exc.value)


def test_allows_adr0029_unpinned_uv_tool_run_mcp_clean_install_source():
    payload = _payload(
        bom={
            "components": [
                {
                    "properties": [
                        {"name": "openaca:component_type", "value": "mcp_server"},
                        {"name": "openaca:identity", "value": "mcp-server/weather"},
                        {"name": "openaca:install_source", "value": "uvx weather-mcp"},
                    ]
                }
            ]
        }
    )

    enforce_remote_upload_contract(payload)


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

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "bom.components[0].properties[2].value" in str(exc.value)
    assert "secret" not in str(exc.value)


def test_allows_posture_evidence_without_rule_specific_allowlist():
    """Posture evidence shape is allowed. Absolute paths in evidence fields
    are rejected (collector redacts before this enforcer runs).
    """
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
                    "manifest_path": "settings.json",
                },
            }
        ]
    )

    enforce_remote_upload_contract(payload)


def test_rejects_absolute_path_inside_observation_evidence_list():
    """Absolute paths inside list-valued evidence fields must be rejected.
    Without list recursion the contract enforcer skips list values entirely,
    so a bare absolute path string in the list would pass unchecked.
    """
    payload = _payload(
        observations=[
            {
                "source": "openaca-skill-audit",
                "source_version": "0.2.0b1",
                "observation_id": "skill.allowed-executable-tool",
                "severity": "LOW",
                "confidence": "high",
                "component_identity": "skill/deploy-helper",
                "subject_coordinate": "sha256:abc123",
                "summary": "Skill declares executable tool access",
                "evidence": {"allowed_tools": ["/Users/alice/deploy.sh"]},
            }
        ]
    )

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "observations[0].evidence.allowed_tools[0]" in str(exc.value)


def test_rejects_embedded_absolute_path_in_observation_evidence_bash_filter():
    """Absolute paths embedded inside Bash filter syntax must be rejected.
    A string like `Bash(/Users/alice/deploy.sh *)` does not start with `/` so
    the whole-string `_is_absolute_path` check would miss it.
    """
    payload = _payload(
        observations=[
            {
                "source": "openaca-skill-audit",
                "source_version": "0.2.0b1",
                "observation_id": "skill.allowed-executable-tool",
                "severity": "LOW",
                "confidence": "high",
                "component_identity": "skill/deploy-helper",
                "subject_coordinate": "sha256:abc123",
                "summary": "Skill declares executable tool access",
                "evidence": {
                    "allowed_tools": ["Bash(/Users/alice/.claude/skills/deploy/run.sh *)"]
                },
            }
        ]
    )

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "observations[0].evidence.allowed_tools[0]" in str(exc.value)
    assert "embedded absolute path" in str(exc.value)


def test_accepts_bash_filter_with_embedded_url():
    """URLs embedded inside Bash filter syntax must NOT be rejected as embedded Unix paths.
    `Bash(curl https://api.example.com/mcp *)` contains a URL path, not a Unix absolute path;
    the enforcer must not false-positive on `host/path` URL components.
    """
    payload = _payload(
        observations=[
            {
                "source": "openaca-skill-audit",
                "source_version": "0.2.0b1",
                "observation_id": "skill.allowed-executable-tool",
                "severity": "LOW",
                "confidence": "high",
                "component_identity": "skill/deploy-helper",
                "subject_coordinate": "sha256:abc123",
                "summary": "Skill declares executable tool access",
                "evidence": {
                    "allowed_tools": ["Bash(curl https://api.example.com/mcp *)"],
                },
            }
        ]
    )

    # Should not raise — a URL path inside a Bash filter is not a Unix absolute path.
    enforce_remote_upload_contract(payload)


def test_rejects_absolute_observation_evidence_path():
    payload = _payload(
        observations=[
            {
                "source": "openaca-skill-audit",
                "source_version": "0.2.0b1",
                "observation_id": "skill.allowed-executable-tool",
                "severity": "LOW",
                "confidence": "high",
                "component_identity": "skill/deploy-helper",
                "subject_coordinate": "sha256:abc123",
                "summary": "Skill declares executable tool access",
                "evidence": {"source_manifest": "/Users/alice/.claude/skills/deploy/SKILL.md"},
            }
        ]
    )

    with pytest.raises(RemoteUploadContractError) as exc:
        enforce_remote_upload_contract(payload)

    assert "observations[0].evidence.source_manifest" in str(exc.value)


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
