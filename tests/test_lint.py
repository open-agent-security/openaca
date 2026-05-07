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
    target = tmp_path / "advisories"
    target.mkdir()
    (target / src.name).write_text(src.read_text())
    runner = CliRunner()
    result = runner.invoke(main, [str(target)])
    assert result.exit_code != 0
    assert "cvss" in result.output.lower()


def test_lint_fails_on_path_mismatch(fixtures_dir, tmp_path):
    src = fixtures_dir / "valid" / "asve-2026-0001.yaml"
    misplaced = tmp_path / "advisories" / "2025" / "ASVE-2026-0001.yaml"
    misplaced.parent.mkdir(parents=True)
    misplaced.write_text(src.read_text())
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path / "advisories")])
    assert result.exit_code != 0
    assert "path" in result.output.lower()


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
