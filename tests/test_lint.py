import yaml
from click.testing import CliRunner

from tools.lint import main


def test_lint_passes_for_valid_fixture(fixtures_dir):
    runner = CliRunner()
    result = runner.invoke(main, [str(fixtures_dir / "valid")])
    assert result.exit_code == 0, result.output


def test_lint_fails_for_invalid_fixture(fixtures_dir):
    runner = CliRunner()
    result = runner.invoke(main, [str(fixtures_dir / "invalid")])
    assert result.exit_code != 0
    assert "exposure" in result.output.lower() or "config" in result.output.lower()


def test_lint_fails_on_bad_cvss(fixtures_dir, tmp_path):
    src = fixtures_dir / "invalid" / "bad-cvss.yaml"
    target_dir = tmp_path / "advisories" / "2026"
    target_dir.mkdir(parents=True)
    (target_dir / "ASVE-2026-9003.yaml").write_text(src.read_text())
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path / "advisories")])
    assert result.exit_code != 0
    assert "cvss" in result.output.lower()


def test_lint_fails_on_overlay_filename_mismatch(fixtures_dir, tmp_path):
    src = fixtures_dir / "valid" / "asve-2026-0001.yaml"
    target = tmp_path / "overlays"
    target.mkdir()
    (target / "wrong-name.yaml").write_text(src.read_text())
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code != 0
    assert "path" in result.output.lower()


def test_lint_fails_on_bad_datetime(fixtures_dir, tmp_path):
    src = fixtures_dir / "invalid" / "bad-datetime.yaml"
    target_dir = tmp_path / "advisories" / "2026"
    target_dir.mkdir(parents=True)
    (target_dir / "ASVE-2026-9004.yaml").write_text(src.read_text())
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path / "advisories")])
    assert result.exit_code != 0
    assert "format" in result.output.lower() or "date" in result.output.lower()


def test_lint_fails_on_path_mismatch(fixtures_dir, tmp_path):
    src = fixtures_dir / "valid" / "asve-2026-0001.yaml"
    misplaced = tmp_path / "overlays" / "ASVE-2026-0002.yaml"
    misplaced.parent.mkdir(parents=True)
    misplaced.write_text(src.read_text())
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path / "overlays")])
    assert result.exit_code != 0
    assert "path" in result.output.lower()


def test_lint_passes_for_v3_severity(fixtures_dir, tmp_path):
    """An advisory with a CVSS v3.1 severity block should pass the linter
    end-to-end. v3 is now an accepted upstream-preservation format."""
    src = fixtures_dir / "valid" / "asve-2026-0001.yaml"
    advisory = yaml.safe_load(src.read_text())
    advisory["severity"] = [
        {
            "type": "CVSS_V3",
            "score": "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H",
        }
    ]
    target_dir = tmp_path / "advisories" / "2026"
    target_dir.mkdir(parents=True)
    (target_dir / "ASVE-2026-0001.yaml").write_text(yaml.safe_dump(advisory))
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path / "advisories")])
    assert result.exit_code == 0, result.output


def test_lint_fails_on_v3_type_with_v4_vector(fixtures_dir, tmp_path):
    """Declared type and vector body must agree — a v3 declaration with a
    v4-shaped score body is malformed and must be rejected."""
    src = fixtures_dir / "valid" / "asve-2026-0001.yaml"
    advisory = yaml.safe_load(src.read_text())
    advisory["severity"] = [
        {
            "type": "CVSS_V3",
            "score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
        }
    ]
    target_dir = tmp_path / "advisories" / "2026"
    target_dir.mkdir(parents=True)
    (target_dir / "ASVE-2026-0001.yaml").write_text(yaml.safe_dump(advisory))
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path / "advisories")])
    assert result.exit_code != 0
    assert "cvss" in result.output.lower()


def test_lint_fails_on_missing_internal_alias(fixtures_dir, tmp_path):
    src = fixtures_dir / "valid" / "asve-2026-0001.yaml"
    target_dir = tmp_path / "advisories" / "2026"
    target_dir.mkdir(parents=True)
    advisory = yaml.safe_load(src.read_text())
    advisory["aliases"].append("ASVE-2026-9999")  # does not exist
    (target_dir / "ASVE-2026-0001.yaml").write_text(yaml.safe_dump(advisory))
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path / "advisories")])
    assert result.exit_code != 0
    assert "ASVE-2026-9999" in result.output


def test_lint_fails_on_duplicate_overlay_id_across_subdirs(fixtures_dir, tmp_path):
    """Two files in different subdirectories with the same overlay id both fail lint."""
    src = fixtures_dir / "valid" / "asve-2026-0001.yaml"
    content = src.read_text()
    # Place the same overlay under two different subdirectory paths.
    for subdir in ("overlays/a", "overlays/b"):
        d = tmp_path / subdir
        d.mkdir(parents=True)
        (d / "ASVE-2026-0001.yaml").write_text(content)
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path / "overlays")])
    assert result.exit_code != 0
    assert "id:" in result.output.lower()


def test_lint_rejects_malformed_overlay_id(tmp_path):
    """An overlay id that doesn't match GHSA-*, CVE-*, or OSV-* must be rejected.
    Guards the SARIF helpUri, export path, and alias-merge contracts (ADR-0009)."""
    overlay = {
        "schema_version": "1.7.5",
        "id": "NOTANID-broken",
        "modified": "2026-01-01T00:00:00Z",
        "database_specific": {
            "asve": {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "confirmed",
            }
        },
    }
    target = tmp_path / "overlays"
    target.mkdir()
    (target / "NOTANID-broken.yaml").write_text(yaml.dump(overlay))
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code != 0
    assert "id:" in result.output.lower()
    assert "upstream" in result.output.lower()


def test_lint_accepts_valid_upstream_id_formats(tmp_path):
    """GHSA-*, CVE-*, OSV-*, PYSEC-*, and MAL-* ids all pass format validation."""
    base = {
        "schema_version": "1.7.5",
        "modified": "2026-01-01T00:00:00Z",
        "database_specific": {
            "asve": {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "confirmed",
            }
        },
    }
    target = tmp_path / "overlays"
    target.mkdir()
    for oid in (
        "GHSA-abcd-ef12-3456",
        "CVE-2026-12345",
        "OSV-2026-1234",
        "PYSEC-2026-123",
        "MAL-2026-1234",
    ):
        (target / f"{oid}.yaml").write_text(yaml.dump({**base, "id": oid}))
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code == 0, result.output


def test_lint_rejects_exposure_type_in_v0(tmp_path):
    """type:exposure is reserved; the schema must reject it even when all other
    required fields are present. Guards the V0 contract in CLAUDE.md."""
    overlay = {
        "schema_version": "1.7.5",
        "id": "GHSA-test-expo",
        "type": "exposure",
        "modified": "2026-01-01T00:00:00Z",
        "database_specific": {
            "asve": {"taxonomies": {"owasp_agentic_top10": ["asi05"]}, "evidence_level": "likely"}
        },
    }
    target = tmp_path / "overlays"
    target.mkdir()
    (target / "GHSA-test-expo.yaml").write_text(yaml.dump(overlay))
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code != 0
    assert "reserved" in result.output.lower()


def test_lint_rejects_config_type_in_v0(tmp_path):
    """type:config is reserved; the schema must reject it even when all other
    required fields are present."""
    overlay = {
        "schema_version": "1.7.5",
        "id": "GHSA-test-conf",
        "type": "config",
        "modified": "2026-01-01T00:00:00Z",
        "database_specific": {
            "asve": {"taxonomies": {"owasp_agentic_top10": ["asi05"]}, "evidence_level": "likely"}
        },
    }
    target = tmp_path / "overlays"
    target.mkdir()
    (target / "GHSA-test-conf.yaml").write_text(yaml.dump(overlay))
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code != 0
    assert "reserved" in result.output.lower()


def test_lint_rejects_threat_kind_on_non_mal_overlay(tmp_path):
    """threat_kind is only valid on MAL-* ids/aliases; reject elsewhere."""
    overlay = {
        "schema_version": "1.7.5",
        "id": "GHSA-test-tkind-aaaa",
        "modified": "2026-01-01T00:00:00Z",
        "database_specific": {
            "asve": {
                "threat_kind": "malicious_package",
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "likely",
            }
        },
    }
    target = tmp_path / "overlays"
    target.mkdir()
    (target / "GHSA-test-tkind-aaaa.yaml").write_text(yaml.dump(overlay))
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code != 0
    assert "threat_kind" in result.output
    assert "MAL-" in result.output


def test_lint_accepts_threat_kind_on_mal_overlay(tmp_path):
    """Sibling test: a MAL-* id makes threat_kind valid."""
    overlay = {
        "schema_version": "1.7.5",
        "id": "MAL-2026-0001",
        "modified": "2026-01-01T00:00:00Z",
        "database_specific": {
            "asve": {
                "threat_kind": "malicious_package",
                "taxonomies": {"owasp_agentic_top10": ["asi04"]},
                "evidence_level": "confirmed",
            }
        },
    }
    target = tmp_path / "overlays"
    target.mkdir()
    (target / "MAL-2026-0001.yaml").write_text(yaml.dump(overlay))
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code == 0, result.output


def test_lint_rejects_empty_taxonomy_bucket_in_overlay(tmp_path):
    overlay = {
        "schema_version": "1.7.5",
        "id": "GHSA-test-empty-bbbb",
        "modified": "2026-01-01T00:00:00Z",
        "database_specific": {
            "asve": {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi05"],
                    "owasp_mcp_top10": [],
                },
                "evidence_level": "likely",
            }
        },
    }
    target = tmp_path / "overlays"
    target.mkdir()
    (target / "GHSA-test-empty-bbbb.yaml").write_text(yaml.dump(overlay))
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code != 0
    assert "empty taxonomy bucket" in result.output
    assert "owasp_mcp_top10" in result.output
