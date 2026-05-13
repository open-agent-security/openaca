import json
import zipfile

import yaml
from click.testing import CliRunner

from tools.seed.__main__ import main


def _write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def _ghsa_record() -> dict:
    return {
        "schema_version": "1.7.5",
        "id": "GHSA-abcd-ef12-3456",
        "aliases": ["CVE-2026-12345"],
        "modified": "2026-05-13T00:00:00Z",
        "summary": "mcp-demo allows command injection",
        "details": "A Model Context Protocol server executes arbitrary commands.",
        "affected": [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}],
        "references": [{"type": "ADVISORY", "url": "https://example.test/advisory"}],
    }


def _mal_record() -> dict:
    return {
        "schema_version": "1.7.5",
        "id": "MAL-2026-1234",
        "modified": "2026-05-13T00:00:00Z",
        "summary": "Malicious code in mcp-runcmd-server",
        "details": "This package executes arbitrary code during install.",
        "affected": [{"package": {"ecosystem": "PyPI", "name": "mcp-runcmd-server"}}],
    }


def test_seed_writes_reviewable_candidate_for_mcp_record(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    candidate = yaml.safe_load((out / "GHSA-abcd-ef12-3456.yaml").read_text(encoding="utf-8"))
    assert candidate["id"] == "GHSA-abcd-ef12-3456"
    assert candidate["aliases"] == ["CVE-2026-12345"]
    assert candidate["_candidate"]["review_status"] == "needs_review"
    assert "package_name_mcp" in candidate["_candidate"]["matched_by"]
    assert candidate["database_specific"]["asve"]["component_type"] == "mcp_server"
    assert candidate["database_specific"]["asve"]["agent_impact"]["code_execution"] is True
    assert candidate["database_specific"]["asve"]["taxonomies"]["owasp_agentic_top10"] == ["asi05"]
    assert candidate["summary"] == "mcp-demo allows command injection"


def test_seed_marks_mal_records_as_malicious_package(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "MAL-2026-1234.json", _mal_record())

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    candidate = yaml.safe_load((out / "MAL-2026-1234.yaml").read_text(encoding="utf-8"))
    asve = candidate["database_specific"]["asve"]
    assert asve["threat_kind"] == "malicious_package"
    assert asve["agent_impact"]["code_execution"] is True
    assert asve["agent_impact"]["credential_exfiltration"] is True


def test_seed_skips_records_already_covered_by_existing_overlay_alias(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())
    (existing / "CVE-2026-12345.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "1.7.5",
                "id": "CVE-2026-12345",
                "aliases": ["GHSA-abcd-ef12-3456"],
                "modified": "2026-05-13T00:00:00Z",
                "database_specific": {"asve": {"component_type": "mcp_server"}},
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    assert not (out / "GHSA-abcd-ef12-3456.yaml").exists()
    assert "1 already curated" in result.output


def test_seed_dry_run_does_not_write_candidates(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    _write_json(dump / "GHSA-abcd-ef12-3456.json", _ghsa_record())

    result = CliRunner().invoke(
        main, [str(dump), "--out", str(out), "--existing", str(existing), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "would write" in result.output
    assert not out.exists()


def test_seed_skips_candidate_with_unsafe_id(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    bad = {
        "schema_version": "1.7.5",
        "id": "../evil",
        "modified": "2026-05-13T00:00:00Z",
        "summary": "mcp server command injection",
        "affected": [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}],
    }
    _write_json(dump / "evil.json", bad)

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0
    assert not (tmp_path / "evil.yaml").exists()
    assert "unsafe" in result.output
    assert "0 candidate" in result.output


def test_seed_deduplicates_aliases_within_run(tmp_path):
    dump = tmp_path / "dump"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    dump.mkdir()
    existing.mkdir()
    ghsa = _ghsa_record()  # id=GHSA-abcd-ef12-3456, aliases=[CVE-2026-12345]
    cve = {
        "schema_version": "1.7.5",
        "id": "CVE-2026-12345",
        "aliases": ["GHSA-abcd-ef12-3456"],
        "modified": "2026-05-13T00:00:00Z",
        "summary": "mcp-demo allows command injection",
        "affected": [{"package": {"ecosystem": "npm", "name": "mcp-demo"}}],
        "references": [{"type": "ADVISORY", "url": "https://example.test/advisory"}],
    }
    # Sorted iteration processes GHSA first, then CVE should be deduplicated
    _write_json(dump / "GHSA-abcd-ef12-3456.json", ghsa)
    _write_json(dump / "CVE-2026-12345.json", cve)

    result = CliRunner().invoke(main, [str(dump), "--out", str(out), "--existing", str(existing)])

    assert result.exit_code == 0, result.output
    written_files = list(out.glob("*.yaml"))
    assert len(written_files) == 1
    assert "1 already curated" in result.output


def test_seed_reads_osv_all_zip(tmp_path):
    zip_path = tmp_path / "all.zip"
    out = tmp_path / "candidates"
    existing = tmp_path / "overlays"
    existing.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("GHSA-abcd-ef12-3456.json", json.dumps(_ghsa_record()))

    result = CliRunner().invoke(
        main, [str(zip_path), "--out", str(out), "--existing", str(existing)]
    )

    assert result.exit_code == 0, result.output
    assert (out / "GHSA-abcd-ef12-3456.yaml").exists()
