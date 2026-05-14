from tools.seed.validator import validate_candidate


def _candidate() -> dict:
    return {
        "schema_version": "1.7.5",
        "id": "GHSA-abcd-ef12-3456",
        "aliases": ["CVE-2026-12345"],
        "summary": "MCP server allows command injection",
        "modified": "2026-05-13T00:00:00Z",
        "_candidate": {
            "matched_by": ["package_name_mcp"],
            "review_status": "needs_review",
        },
        "_evidence": [{"field": "summary", "quote": "command injection"}],
        "database_specific": {
            "asve": {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "likely",
            }
        },
    }


def test_validate_candidate_accepts_reviewable_candidate():
    assert validate_candidate(_candidate()) == []


def test_validate_candidate_requires_candidate_metadata():
    candidate = _candidate()
    candidate.pop("_candidate")

    errors = validate_candidate(candidate)

    assert any("_candidate" in e for e in errors)


def test_validate_candidate_rejects_bad_taxonomy_code():
    candidate = _candidate()
    candidate["database_specific"]["asve"]["taxonomies"]["owasp_agentic_top10"] = ["ASI05"]

    errors = validate_candidate(candidate)

    assert any("schema" in e and "taxonomies" in e for e in errors)


def test_validate_candidate_rejects_non_canonical_asve_fields():
    candidate = _candidate()
    candidate["database_specific"]["asve"]["agent_impact"] = {"code_execution": True}

    errors = validate_candidate(candidate)

    assert any("schema" in e and "agent_impact" in e for e in errors)


def test_validate_candidate_allows_upstream_owned_fields_because_promotion_strips_them():
    candidate = _candidate()
    candidate["details"] = "Upstream-owned details"
    candidate["affected"] = [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}]
    candidate["severity"] = [{"type": "CVSS_V3", "score": "not a vector"}]

    assert validate_candidate(candidate) == []
