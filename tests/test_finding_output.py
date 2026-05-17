from tools.component_ref import ComponentRef
from tools.finding_output import finding_to_output
from tools.matcher import Finding


def test_finding_to_output_explains_direct_mcp_component():
    ref = ComponentRef(
        ecosystem="npm",
        name="@modelcontextprotocol/server-filesystem",
        version="1.0.2",
        source_manifest=".mcp.json",
        source_locator="$.mcpServers.filesystem",
        extra={
            "component_type": "mcp_server",
            "runtime_hosts": ["claude-code"],
            "declared_by": {"kind": "manifest", "path": ".mcp.json"},
        },
    )
    finding = Finding(
        advisory_id="GHSA-xxxx-yyyy-zzzz",
        component=ref,
        confidence="high",
        reason="matched range",
    )
    advisory = {
        "id": "GHSA-xxxx-yyyy-zzzz",
        "aliases": ["CVE-2026-1234"],
        "summary": "Filesystem MCP vulnerability",
    }

    out = finding_to_output(finding, advisory)

    assert out["finding_type"] == "vulnerability"
    assert out["component"]["type"] == "mcp_server"
    assert out["component"]["name"] == "@modelcontextprotocol/server-filesystem"
    assert out["component"]["source"]["purl"] == (
        "pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.2"
    )
    assert out["active_in"] == ["claude-code"]
    assert out["declared_by"]["kind"] == "manifest"
    assert out["component_path"] == [
        {"type": "mcp_server", "name": "@modelcontextprotocol/server-filesystem"}
    ]
    assert out["matched_advisory"]["id"] == "GHSA-xxxx-yyyy-zzzz"
