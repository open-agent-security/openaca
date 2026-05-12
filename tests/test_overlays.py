from tools.overlays import apply_overlays


def test_apply_overlays_merges_agent_context_by_alias_intersection():
    osv_record = {
        "id": "GHSA-3q26-f695-pp76",
        "aliases": ["CVE-2025-53107"],
        "summary": "upstream summary",
        "affected": [{"package": {"ecosystem": "npm", "name": "@cyanheads/git-mcp-server"}}],
        "database_specific": {"asve": {"source": "osv.dev"}},
    }
    overlay = {
        "id": "CVE-2025-53107",
        "aliases": ["GHSA-3q26-f695-pp76"],
        "database_specific": {
            "asve": {
                "component_type": "mcp_server",
                "surfaces": ["tool_invocation", "stdio"],
                "agent_impact": {"credential_exfiltration": True},
            }
        },
    }

    merged = apply_overlays([osv_record], [overlay])

    assert merged[0]["id"] == "GHSA-3q26-f695-pp76"
    assert merged[0]["summary"] == "upstream summary"
    assert merged[0]["affected"] == osv_record["affected"]
    asve = merged[0]["database_specific"]["asve"]
    assert asve["source"] == "osv.dev"
    assert asve["overlay_source"] == "asve.dev"
    assert asve["component_type"] == "mcp_server"
    assert asve["surfaces"] == ["tool_invocation", "stdio"]


def test_apply_overlays_ignores_unrelated_records():
    record = {"id": "GHSA-1111", "database_specific": {"asve": {"source": "osv.dev"}}}
    overlay = {
        "id": "GHSA-2222",
        "database_specific": {"asve": {"component_type": "mcp_server"}},
    }

    assert apply_overlays([record], [overlay]) == [record]
