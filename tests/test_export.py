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


def test_modified_id_csv_lists_advisories(sample_corpus, tmp_path, schema_path):
    import csv as csvlib

    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    csv_path = dist / "modified_id.csv"
    assert csv_path.is_file()
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csvlib.DictReader(f))
    assert any(r["id"] == "ASVE-2026-0001" for r in rows)
    row = next(r for r in rows if r["id"] == "ASVE-2026-0001")
    assert row["modified"]


def test_modified_id_csv_sorted_by_id(tmp_path, schema_path, fixtures_dir):
    """Multiple advisories must serialize sorted by id for stable diffs."""
    advisories_dir = tmp_path / "advisories" / "2026"
    advisories_dir.mkdir(parents=True)
    src = (fixtures_dir / "valid" / "asve-2026-0001.yaml").read_text()
    (advisories_dir / "ASVE-2026-0002.yaml").write_text(
        src.replace("ASVE-2026-0001", "ASVE-2026-0002")
    )
    (advisories_dir / "ASVE-2026-0001.yaml").write_text(src)
    dist = tmp_path / "dist"
    build(tmp_path / "advisories", schema_path=schema_path, dist=dist)
    import csv as csvlib

    with (dist / "modified_id.csv").open(encoding="utf-8") as f:
        ids = [r["id"] for r in csvlib.DictReader(f)]
    assert ids == ["ASVE-2026-0001", "ASVE-2026-0002"]


def test_index_json_flat_summary(sample_corpus, tmp_path, schema_path):
    """index.json is the static API surface — flat list with key fields."""
    import json as jsonlib

    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    index = jsonlib.loads((dist / "index.json").read_text(encoding="utf-8"))
    assert isinstance(index, list)
    entry = next(e for e in index if e["id"] == "ASVE-2026-0001")
    assert entry["summary"]
    assert entry["modified"]
    assert isinstance(entry["affected_ecosystems"], list)
    assert "npm" in entry["affected_ecosystems"]


def test_all_zip_contains_each_advisory(sample_corpus, tmp_path, schema_path):
    import zipfile

    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    zip_path = dist / "all.zip"
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "advisories/2026/ASVE-2026-0001.json" in names
    assert "schema/asve.schema.json" in names
    assert "modified_id.csv" in names
    assert "index.json" in names


def test_all_zip_does_not_contain_itself(sample_corpus, tmp_path, schema_path):
    """Sanity: the zip must not recursively include all.zip in itself."""
    import zipfile

    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    with zipfile.ZipFile(dist / "all.zip") as zf:
        assert "all.zip" not in zf.namelist()


def test_export_emits_per_advisory_html(sample_corpus, tmp_path, schema_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    advisory_html = (dist / "advisories" / "2026" / "ASVE-2026-0001.html").read_text(
        encoding="utf-8"
    )
    assert "ASVE-2026-0001" in advisory_html
    assert "<!doctype html>" in advisory_html
    assert "../../style.css" in advisory_html


def test_export_emits_index_html_grouped_by_year(sample_corpus, tmp_path, schema_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    index_html = (dist / "index.html").read_text(encoding="utf-8")
    assert "ASVE-2026-0001" in index_html
    assert "<details" in index_html
    assert "<summary>2026" in index_html
    assert 'id="filter"' in index_html


def test_export_copies_style_css(sample_corpus, tmp_path, schema_path):
    dist = tmp_path / "dist"
    build(sample_corpus / "advisories", schema_path=schema_path, dist=dist)
    css = (dist / "style.css").read_text(encoding="utf-8")
    assert "body" in css


def test_html_autoescapes_advisory_content(tmp_path, schema_path, fixtures_dir):
    """Jinja autoescape must neutralize HTML in advisory.summary."""
    advisories_dir = tmp_path / "advisories" / "2026"
    advisories_dir.mkdir(parents=True)
    src = (fixtures_dir / "valid" / "asve-2026-0001.yaml").read_text()
    src = src.replace(
        'summary: "Command injection in @cyanheads/git-mcp-server"',
        'summary: "<script>alert(1)</script> traversal"',
    )
    (advisories_dir / "ASVE-2026-0001.yaml").write_text(src)
    dist = tmp_path / "dist"
    build(tmp_path / "advisories", schema_path=schema_path, dist=dist)
    page = (dist / "advisories" / "2026" / "ASVE-2026-0001.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_index_html_groups_by_year_descending(tmp_path, schema_path, fixtures_dir):
    """Year groups must be sorted newest-first so the most recent year is on top."""
    advisories_dir_2026 = tmp_path / "advisories" / "2026"
    advisories_dir_2027 = tmp_path / "advisories" / "2027"
    advisories_dir_2026.mkdir(parents=True)
    advisories_dir_2027.mkdir(parents=True)
    src = (fixtures_dir / "valid" / "asve-2026-0001.yaml").read_text()
    (advisories_dir_2026 / "ASVE-2026-0001.yaml").write_text(src)
    (advisories_dir_2027 / "ASVE-2027-0001.yaml").write_text(
        src.replace("ASVE-2026-0001", "ASVE-2027-0001")
    )
    dist = tmp_path / "dist"
    build(tmp_path / "advisories", schema_path=schema_path, dist=dist)
    index_html = (dist / "index.html").read_text(encoding="utf-8")
    pos_2027 = index_html.find("2027")
    pos_2026 = index_html.find("2026")
    assert 0 <= pos_2027 < pos_2026
