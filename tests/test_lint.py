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
