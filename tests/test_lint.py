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


def test_lint_rejects_exposure_type_in_v0(tmp_path):
    """type:exposure is reserved; the schema must reject it even when all other
    required fields are present. Guards the V0 contract in CLAUDE.md."""
    overlay = {
        "schema_version": "1.7.5",
        "id": "GHSA-test-expo",
        "type": "exposure",
        "modified": "2026-01-01T00:00:00Z",
        "database_specific": {"asve": {"component_type": "mcp_server"}},
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
        "database_specific": {"asve": {"component_type": "mcp_server"}},
    }
    target = tmp_path / "overlays"
    target.mkdir()
    (target / "GHSA-test-conf.yaml").write_text(yaml.dump(overlay))
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code != 0
    assert "reserved" in result.output.lower()
