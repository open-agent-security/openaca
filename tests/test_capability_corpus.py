import json
from pathlib import Path

import jsonschema
import yaml

from tools.capability_corpus import load_capability_corpus


def test_seed_entry_validates():
    schema = json.loads(Path("schema/openaca-capability.schema.json").read_text())
    doc = yaml.safe_load(Path("capabilities/mcp-server-filesystem.yaml").read_text())
    jsonschema.validate(doc, schema)  # must not raise
    assert doc["identity"].startswith("mcp-server/")
    assert doc["last_reviewed"] and doc["reviewed_version"]
    assert all(c["name"] for c in doc["capabilities"])


def test_lookup_by_match_coordinate_ignores_local_alias():
    # The seed's match_coordinate is the npm package coordinate. A ref that
    # aliases the same server under a different local config name must still
    # get the curated capabilities via the coordinate, not the alias.
    corpus = load_capability_corpus()   # defaults to capabilities/
    caps = corpus.lookup("mcp-server/some-local-alias",
                         match_coordinate="npm/@modelcontextprotocol/server-filesystem")
    assert {c.name for c in caps} >= {"file_read", "file_write"}
    assert all(c.method == "curated" and c.source == "openaca" for c in caps)
    assert any(e.get("kind") == "curated_review" for c in caps for e in c.evidence)


def test_lookup_unknown_identity_returns_empty():
    assert load_capability_corpus().lookup("mcp-server/does-not-exist") == []


def test_constrained_record_not_returned_by_identity_alone():
    # A record with a match_coordinate must never surface via identity alone
    # -- otherwise an unrelated component that happens to reuse the curated
    # identity string as its local config alias would inherit capabilities
    # it was never reviewed for.
    corpus = load_capability_corpus()
    assert corpus.lookup("mcp-server/filesystem") == []
    assert corpus.lookup("mcp-server/filesystem",
                         match_coordinate="npm/some-other-package") == []


def test_lookup_identity_only_for_unconstrained_records(tmp_path):
    (tmp_path / "x.yaml").write_text(
        "identity: mcp-server/x\nlast_reviewed: '2026-07-03'\n"
        "reviewed_version: '1.0'\ncapabilities:\n"
        "  - {name: file_read, execution_locus: local, confidence: high, evidence: []}\n"
    )
    corpus = load_capability_corpus(root=tmp_path)
    # No derivable coordinate -> identity index is queried.
    assert {c.name for c in corpus.lookup("mcp-server/x")} == {"file_read"}


def test_identity_only_record_suppressed_when_ref_resolves_to_coordinate(tmp_path):
    # An unconstrained record keyed by identity `mcp-server/filesystem`. A ref
    # that resolves to a real package coordinate but whose local alias collides
    # with that identity string must NOT inherit the record: once a coordinate
    # is derivable, the alias is untrustworthy and the identity index is skipped.
    (tmp_path / "fs.yaml").write_text(
        "identity: mcp-server/filesystem\nlast_reviewed: '2026-07-03'\n"
        "reviewed_version: '1.0'\ncapabilities:\n"
        "  - {name: file_read, execution_locus: local, confidence: high, evidence: []}\n"
    )
    corpus = load_capability_corpus(root=tmp_path)
    assert corpus.lookup("mcp-server/filesystem",
                         match_coordinate="npm/some-other-package") == []


def test_corpus_discovers_nested_records(tmp_path):
    # Recursive discovery: a record in a subdirectory is still loaded.
    nested = tmp_path / "npm" / "@scope"
    nested.mkdir(parents=True)
    (nested / "name.yaml").write_text(
        "identity: package/npm/@scope/name\nlast_reviewed: '2026-07-03'\n"
        "reviewed_version: '1.0'\ncapabilities:\n"
        "  - {name: file_read, execution_locus: local, confidence: high, evidence: []}\n"
    )
    corpus = load_capability_corpus(root=tmp_path)
    assert {c.name for c in corpus.lookup("package/npm/@scope/name")} == {"file_read"}
