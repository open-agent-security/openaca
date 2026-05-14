from pathlib import Path

import yaml

from tools.seed.validator import validate_candidate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "candidates"


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


def test_validate_candidate_rejects_threat_kind_on_non_mal_record():
    """threat_kind is seeder-owned and only valid on MAL-* ids/aliases."""
    candidate = _candidate()
    candidate["database_specific"]["asve"]["threat_kind"] = "malicious_package"

    errors = validate_candidate(candidate)

    assert any(
        "threat_kind" in e and "MAL-" in e for e in errors
    ), f"expected actionable threat_kind error, got: {errors}"


def test_validate_candidate_accepts_threat_kind_on_mal_record_id():
    candidate = _candidate()
    candidate["id"] = "MAL-2026-0001"
    candidate["database_specific"]["asve"]["threat_kind"] = "malicious_package"

    assert validate_candidate(candidate) == []


def test_validate_candidate_accepts_threat_kind_on_mal_alias():
    candidate = _candidate()
    candidate["aliases"] = ["MAL-2026-0042"]
    candidate["database_specific"]["asve"]["threat_kind"] = "malicious_package"

    assert validate_candidate(candidate) == []


def test_validate_candidate_rejects_empty_taxonomy_array():
    candidate = _candidate()
    candidate["database_specific"]["asve"]["taxonomies"]["owasp_mcp_top10"] = []

    errors = validate_candidate(candidate)

    assert any(
        "empty taxonomy bucket" in e and "owasp_mcp_top10" in e for e in errors
    ), f"expected empty-bucket error naming owasp_mcp_top10, got: {errors}"


def test_validate_candidate_rejects_empty_supplemental_taxonomies():
    candidate = _candidate()
    candidate["database_specific"]["asve"]["taxonomies"]["supplemental_taxonomies"] = {}

    errors = validate_candidate(candidate)

    assert any(
        "empty taxonomy bucket" in e and "supplemental_taxonomies" in e for e in errors
    ), f"expected empty-bucket error naming supplemental_taxonomies, got: {errors}"


def test_fixture_flowise_nano_bad_is_rejected_with_actionable_errors():
    """The literal nano-style annotation for GHSA-mq53-pc65-wjc4 must be
    rejected, and each violation must surface in errors with wording
    specific enough for a reviewer or agent to self-correct.
    """
    candidate = yaml.safe_load(
        (FIXTURES / "flowise-nano-bad.yaml").read_text(encoding="utf-8")
    )

    errors = validate_candidate(candidate)

    joined = "\n".join(errors)
    # threat_kind on non-MAL record
    assert "threat_kind" in joined and "MAL-" in joined, joined
    # at least one empty-bucket error, naming a specific bucket
    assert "empty taxonomy bucket" in joined, joined
    assert (
        "owasp_mcp_top10" in joined
        or "owasp_agentic_skills_top10" in joined
        or "supplemental_taxonomies" in joined
    ), joined


def test_fixture_flowise_corrected_good_validates():
    """Forward-compatible record of 'this is what a correct annotation
    looks like': opus-style asi03 + llm02:2025, no threat_kind, no
    empty taxonomy buckets, no ATLAS supply-chain misclassification.
    """
    candidate = yaml.safe_load(
        (FIXTURES / "flowise-corrected-good.yaml").read_text(encoding="utf-8")
    )

    assert validate_candidate(candidate) == []
