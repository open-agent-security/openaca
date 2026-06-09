import json

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError


@pytest.fixture
def schema(schema_path):
    return json.loads(schema_path.read_text())


@pytest.fixture
def sample_valid(fixtures_dir):
    return yaml.safe_load((fixtures_dir / "valid" / "cve-2026-0001.yaml").read_text())


def test_schema_is_valid_jsonschema(schema):
    Draft202012Validator.check_schema(schema)


def test_sample_advisory_passes_schema(schema, sample_valid):
    Draft202012Validator(schema).validate(sample_valid)


def test_schema_accepts_minimal_openaca_overlay(schema, sample_valid):
    Draft202012Validator(schema).validate(sample_valid)


def test_schema_accepts_taxonomies_block_and_malicious_package_threat_kind(schema, sample_valid):
    advisory = dict(sample_valid)
    openaca = dict(advisory["database_specific"]["openaca"])
    openaca["threat_kind"] = "malicious_package"
    openaca["taxonomies"] = {
        "owasp_agentic_top10": ["asi02", "asi05"],
        "owasp_mcp_top10": ["mcp05:2025"],
        "owasp_agentic_skills_top10": ["ast03:2025"],
        "owasp_llm_top10": ["llm01:2025"],
        "mitre_atlas": ["AML.T0051"],
    }
    advisory["database_specific"]["openaca"] = openaca

    Draft202012Validator(schema).validate(advisory)


def test_schema_accepts_openaca_match_coordinate(schema, sample_valid):
    advisory = dict(sample_valid)
    openaca = dict(advisory["database_specific"]["openaca"])
    openaca["match_coordinate"] = "skills.sh:anthropics/skills/frontend-design"
    advisory["database_specific"]["openaca"] = openaca

    Draft202012Validator(schema).validate(advisory)


def test_schema_rejects_unknown_threat_kind(schema, sample_valid):
    advisory = dict(sample_valid)
    openaca = dict(advisory["database_specific"]["openaca"])
    openaca["threat_kind"] = "ssrf_via_mcp_tool"
    advisory["database_specific"]["openaca"] = openaca

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(advisory)


@pytest.mark.parametrize(
    "field,value",
    [
        ("component_identity", "claude-hook/settings/project/PreToolUse/0"),
        ("component_type", "mcp_server"),
        ("surfaces", ["tool_invocation"]),
        ("agent_impact", {"code_execution": True}),
    ],
)
def test_schema_rejects_non_canonical_openaca_fields(schema, sample_valid, field, value):
    advisory = dict(sample_valid)
    openaca = dict(advisory["database_specific"]["openaca"])
    openaca[field] = value
    advisory["database_specific"]["openaca"] = openaca

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(advisory)


@pytest.mark.parametrize(
    "ref_type",
    [
        # Pre-existing types — keep validating to guard against accidental removal.
        "ADVISORY",
        "ARTICLE",
        "FIX",
        "PACKAGE",
        "REPORT",
        "WEB",
        # OSV-spec types previously rejected — needed to ingest upstream records
        # that cite HN discussions, evidence dumps, git commits, etc.
        "DETECTION",
        "DISCUSSION",
        "EVIDENCE",
        "GIT",
        "INTRODUCED",
    ],
)
def test_schema_accepts_osv_reference_types(schema, sample_valid, ref_type):
    advisory = dict(sample_valid)
    advisory["references"] = [{"type": ref_type, "url": "https://example.test/ref"}]
    Draft202012Validator(schema).validate(advisory)


def test_schema_rejects_unknown_reference_type(schema, sample_valid):
    advisory = dict(sample_valid)
    advisory["references"] = [{"type": "BLOGPOST", "url": "https://example.test/ref"}]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(advisory)


def test_schema_rejects_malformed_taxonomy_codes(schema, sample_valid):
    advisory = dict(sample_valid)
    openaca = dict(advisory["database_specific"]["openaca"])
    openaca["taxonomies"] = {"owasp_agentic_top10": ["ASI02"]}
    advisory["database_specific"]["openaca"] = openaca

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(advisory)


@pytest.mark.parametrize("name", ["exposure-not-allowed", "config-not-allowed"])
def test_v0_rejects_non_vulnerability_types(schema, fixtures_dir, name):
    advisory = yaml.safe_load((fixtures_dir / "invalid" / f"{name}.yaml").read_text())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(advisory)


@pytest.mark.parametrize(
    "sev_type,score",
    [
        # v3 declaration with a v4 vector body
        ("CVSS_V3", "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
        # v4 declaration with a v3 vector body
        ("CVSS_V4", "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H"),
    ],
)
def test_schema_rejects_mismatched_severity_type_and_score(schema, sample_valid, sev_type, score):
    advisory = dict(sample_valid)
    advisory["severity"] = [{"type": sev_type, "score": score}]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(advisory)
