import json

import pytest
import yaml

from tools.export import build, load_corpus


@pytest.fixture
def sample_corpus(tmp_path, fixtures_dir):
    advisories_dir = tmp_path / "advisories" / "2026"
    advisories_dir.mkdir(parents=True)
    src = fixtures_dir / "valid" / "asve-2026-0001.yaml"
    (advisories_dir / "ASVE-2026-0001.yaml").write_text(src.read_text())
    return tmp_path


def test_load_corpus_returns_one_advisory(sample_corpus):
    corpus = load_corpus(sample_corpus / "advisories")
    assert len(corpus) == 1
    assert corpus[0]["id"] == "ASVE-2026-0001"


def test_build_emits_json_per_advisory(sample_corpus, tmp_path, schema_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    json_path = dist / "advisories" / "2026" / "ASVE-2026-0001.json"
    assert json_path.is_file()
    record = json.loads(json_path.read_text())
    src = yaml.safe_load(
        (sample_corpus / "advisories" / "2026" / "ASVE-2026-0001.yaml").read_text()
    )
    assert record == src


def test_build_emits_utf8_and_trailing_newline(sample_corpus, tmp_path, schema_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    raw = (dist / "advisories" / "2026" / "ASVE-2026-0001.json").read_bytes()
    assert raw.endswith(b"\n")
    raw.decode("utf-8")  # raises if not valid UTF-8


def test_build_copies_schema(sample_corpus, tmp_path, schema_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    assert (dist / "schema" / "asve.schema.json").is_file()


def test_build_is_idempotent_clean(sample_corpus, tmp_path, schema_path):
    """Re-running build must produce the same output (cleans dist first)."""
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    stale = dist / "advisories" / "2026" / "ASVE-9999-0099.json"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("{}")
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    assert not stale.exists()
