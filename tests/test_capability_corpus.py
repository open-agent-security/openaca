import json
from pathlib import Path

import jsonschema
import yaml


def test_seed_entry_validates():
    schema = json.loads(Path("schema/openaca-capability.schema.json").read_text())
    doc = yaml.safe_load(Path("capabilities/mcp-server-filesystem.yaml").read_text())
    jsonschema.validate(doc, schema)  # must not raise
    assert doc["identity"].startswith("mcp-server/")
    assert doc["last_reviewed"] and doc["reviewed_version"]
    assert all(c["name"] for c in doc["capabilities"])
