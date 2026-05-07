import json

import pytest
import yaml
from jsonschema import Draft202012Validator


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
