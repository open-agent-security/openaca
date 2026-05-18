import json

import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from tools.promote import main, project_candidate_to_overlay


def _candidate() -> dict:
    return {
        "schema_version": "1.7.5",
        "id": "GHSA-abcd-ef12-3456",
        "aliases": ["CVE-2026-12345"],
        "summary": "Upstream-owned summary",
        "details": "Upstream-owned details",
        "modified": "2026-05-13T00:00:00Z",
        "_candidate": {
            "matched_by": ["package_name_mcp"],
            "review_status": "needs_review",
            "upstream_summary": "MCP server command injection",
        },
        "_evidence": [{"field": "summary", "quote": "command injection"}],
        "database_specific": {
            "openaca": {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "likely",
            }
        },
    }


def test_project_candidate_to_overlay_strips_candidate_and_upstream_owned_fields(schema_path):
    overlay = project_candidate_to_overlay(_candidate())

    assert overlay == {
        "schema_version": "1.7.5",
        "id": "GHSA-abcd-ef12-3456",
        "aliases": ["CVE-2026-12345"],
        "modified": "2026-05-13T00:00:00Z",
        "database_specific": {
            "openaca": {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "likely",
            }
        },
    }
    assert "_candidate" not in overlay
    assert "_evidence" not in overlay
    assert "summary" not in overlay
    assert "details" not in overlay

    schema = json.loads(schema_path.read_text())
    Draft202012Validator(schema).validate(overlay)


def test_project_candidate_to_overlay_rejects_missing_openaca_block():
    candidate = _candidate()
    candidate["database_specific"] = {}

    with pytest.raises(ValueError, match="database_specific.openaca"):
        project_candidate_to_overlay(candidate)


def test_promote_cli_writes_overlay_by_id(tmp_path):
    candidate_dir = tmp_path / "candidates"
    overlays_dir = tmp_path / "overlays"
    candidate_dir.mkdir()
    source = candidate_dir / "GHSA-abcd-ef12-3456.yaml"
    source.write_text(yaml.safe_dump(_candidate(), sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(main, [str(source), "--overlays", str(overlays_dir)])

    assert result.exit_code == 0, result.output
    target = overlays_dir / "GHSA-abcd-ef12-3456.yaml"
    assert target.exists()
    promoted = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert promoted["id"] == "GHSA-abcd-ef12-3456"
    assert "_candidate" not in promoted
    assert "summary" not in promoted
    # Source candidate is removed atomically with overlay write.
    assert not source.exists()
    assert f"removed {source}" in result.output


def test_promote_rejects_unsafe_overlay_id(tmp_path):
    candidate_dir = tmp_path / "candidates"
    overlays_dir = tmp_path / "overlays"
    candidate_dir.mkdir()
    bad = _candidate()
    bad["id"] = "../evil"
    source = candidate_dir / "evil.yaml"
    source.write_text(yaml.safe_dump(bad, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(main, [str(source), "--overlays", str(overlays_dir)])

    assert result.exit_code != 0
    assert not (tmp_path / "evil.yaml").exists()
    # Failed promotion must NOT remove the source candidate.
    assert source.exists()


def test_promote_rejects_non_upstream_id(tmp_path):
    candidate_dir = tmp_path / "candidates"
    overlays_dir = tmp_path / "overlays"
    candidate_dir.mkdir()
    bad = _candidate()
    bad["id"] = "GO-2026-1234"
    source = candidate_dir / "GO-2026-1234.yaml"
    source.write_text(yaml.safe_dump(bad, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(main, [str(source), "--overlays", str(overlays_dir)])

    assert result.exit_code != 0
    assert "not a recognized upstream ID family" in result.output
    assert not (overlays_dir / "GO-2026-1234.yaml").exists()
    assert source.exists()


def test_promote_rejects_malformed_modified_datetime(tmp_path):
    candidate_dir = tmp_path / "candidates"
    overlays_dir = tmp_path / "overlays"
    candidate_dir.mkdir()
    bad = _candidate()
    bad["modified"] = "not-a-date"
    source = candidate_dir / "GHSA-abcd-ef12-3456.yaml"
    source.write_text(yaml.safe_dump(bad, sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(main, [str(source), "--overlays", str(overlays_dir)])

    assert result.exit_code != 0
    assert not (overlays_dir / "GHSA-abcd-ef12-3456.yaml").exists()
    assert source.exists()


def test_promote_rejects_when_source_and_target_are_same_file(tmp_path):
    overlays_dir = tmp_path / "overlays"
    overlays_dir.mkdir()
    source = overlays_dir / "GHSA-abcd-ef12-3456.yaml"
    source.write_text(yaml.safe_dump(_candidate(), sort_keys=False), encoding="utf-8")

    result = CliRunner().invoke(main, [str(source), "--overlays", str(overlays_dir), "--force"])

    assert result.exit_code != 0
    assert "same file" in result.output
    # Must not delete the file it was given.
    assert source.exists()


def test_promote_cli_refuses_to_overwrite_existing_overlay(tmp_path):
    candidate_dir = tmp_path / "candidates"
    overlays_dir = tmp_path / "overlays"
    candidate_dir.mkdir()
    overlays_dir.mkdir()
    source = candidate_dir / "GHSA-abcd-ef12-3456.yaml"
    source.write_text(yaml.safe_dump(_candidate(), sort_keys=False), encoding="utf-8")
    target = overlays_dir / "GHSA-abcd-ef12-3456.yaml"
    target.write_text("existing: true\n", encoding="utf-8")

    result = CliRunner().invoke(main, [str(source), "--overlays", str(overlays_dir)])

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert target.read_text(encoding="utf-8") == "existing: true\n"
    assert source.exists()
