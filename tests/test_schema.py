import json

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError


@pytest.fixture
def schema(schema_path):
    return json.loads(schema_path.read_text())


@pytest.fixture
def sample_valid(fixtures_dir):
    return yaml.safe_load((fixtures_dir / "valid" / "asve-2026-0001.yaml").read_text())


def test_schema_is_valid_jsonschema(schema):
    Draft202012Validator.check_schema(schema)


def test_sample_advisory_passes_schema(schema, sample_valid):
    Draft202012Validator(schema).validate(sample_valid)


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
