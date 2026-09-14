import json
from dataclasses import asdict

import pytest

from tools.posture import run_posture_rules

RULE_ID = "openaca-posture-mcp-header-credential"


def check(tmp_path, entry, envelope="mcpServers"):
    servers = {"demo": entry}
    manifest = {envelope: servers} if envelope else servers
    return [
        f
        for f in run_posture_rules([], [(tmp_path / "mcp.json", manifest)])
        if f.rule_id == RULE_ID
    ]


@pytest.mark.parametrize("envelope", ["mcpServers", "servers", None])
@pytest.mark.parametrize("header", ["Authorization", "authorization", "AUTHORIZATION", "X-Api-Key"])
def test_literal_header_is_reported_without_its_value(tmp_path, envelope, header):
    token = "dummy-plain-text-token"
    findings = check(
        tmp_path,
        {"url": "https://example.test/mcp", "headers": {header: f"Bearer {token}"}},
        envelope,
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == "medium"
    assert finding.confidence == "high"
    assert finding.standards.cwe == ["CWE-798"]
    assert token not in json.dumps(asdict(finding))
    assert finding.evidence == {"fields": [f"headers.{header.lower()}"]}


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        None,
        123,
        "Bearer",
        "Bearer ",
        "${TOKEN}",
        "Bearer ${TOKEN}",
        "Bearer ${TOKEN:-}",
        "Bearer ${env:TOKEN}",
        "Bearer ${input:token}",
    ],
)
def test_empty_and_indirect_credentials_are_not_reported(tmp_path, value):
    assert (
        check(tmp_path, {"url": "https://example.test/mcp", "headers": {"Authorization": value}})
        == []
    )


@pytest.mark.parametrize(
    "value",
    [
        "Bearer dummy",
        "Basic dXNlcjpwYXNz",
        "Bearer ${TOKEN:-literal-fallback}",
        "Bearer literal-${TOKEN}",
    ],
)
def test_literals_are_not_hidden_by_placeholder_or_interpolation(tmp_path, value):
    assert (
        len(
            check(
                tmp_path, {"url": "https://example.test/mcp", "headers": {"Authorization": value}}
            )
        )
        == 1
    )


def test_only_authentication_fields_and_remote_servers(tmp_path):
    assert (
        check(
            tmp_path,
            {
                "url": "https://example.test/mcp",
                "headers": {"Accept": "application/json", "X-Region": "us-east-1"},
            },
        )
        == []
    )
    assert (
        check(tmp_path, {"command": "server", "headers": {"Authorization": "Bearer literal"}}) == []
    )
    assert (
        check(
            tmp_path,
            {
                "url": "https://example.test/mcp",
                "disabled": True,
                "headers": {"Authorization": "Bearer literal"},
            },
        )
        == []
    )


def test_codex_static_headers_are_literal_even_with_variable_syntax(tmp_path):
    assert (
        len(
            check(
                tmp_path,
                {
                    "url": "https://example.test/mcp",
                    "http_headers": {"Authorization": "Bearer ${TOKEN}"},
                },
            )
        )
        == 1
    )
    assert (
        check(
            tmp_path,
            {
                "url": "https://example.test/mcp",
                "env_http_headers": {"Authorization": "TOKEN"},
                "bearer_token_env_var": "TOKEN",
            },
        )
        == []
    )


def test_multiple_headers_make_one_finding(tmp_path):
    findings = check(
        tmp_path,
        {
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer literal", "X-Api-Key": "literal"},
        },
    )
    assert len(findings) == 1
    assert findings[0].evidence["fields"] == ["headers.authorization", "headers.x-api-key"]


def test_graph_collector_reads_only_composed_servers_without_mutating_refs(tmp_path):
    from tools.component_ref import ComponentRef
    from tools.posture import collect_cursor_mcp_manifests

    path = tmp_path / "mcp.json"
    token = "dummy-unexportable-token"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    name: {"url": "https://example.test/mcp", "headers": {"Authorization": token}}
                    for name in ("selected.with.dots", "excluded")
                }
            }
        )
    )
    ref = ComponentRef(
        name="selected.with.dots",
        source_manifest=str(path),
        extra={"component_type": "mcp_server", "url": "https://example.test/mcp"},
    )
    original = json.dumps(asdict(ref))
    manifests = collect_cursor_mcp_manifests([], refs=[ref])
    findings = run_posture_rules([ref], manifests)
    assert len(findings) == 1
    assert findings[0].component_label == "mcp-server/selected.with.dots"
    assert json.dumps(asdict(ref)) == original
    assert token not in json.dumps(asdict(findings[0]))


@pytest.mark.parametrize("headers", [None, "not-an-object", [], {"Authorization": []}])
def test_malformed_headers_do_not_crash(tmp_path, headers):
    assert check(tmp_path, {"url": "https://example.test/mcp", "headers": headers}) == []


def test_sarif_does_not_contain_header_values(tmp_path):
    from tools.sarif import to_sarif

    token = "dummy-token-not-for-reporting"
    findings = check(
        tmp_path, {"url": "https://example.test/mcp", "headers": {"Authorization": token}}
    )
    output = json.dumps(to_sarif([], {}, posture_findings=findings))
    assert RULE_ID in output
    assert token not in output
