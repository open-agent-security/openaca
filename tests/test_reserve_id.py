from pathlib import Path

from click.testing import CliRunner

from tools.reserve_id import main


def write_advisory(dir: Path, advisory_id: str) -> None:
    year = advisory_id.split("-")[1]
    target = dir / year
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{advisory_id}.yaml").write_text(f"id: {advisory_id}\n")


def test_reserve_id_starts_at_0001(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path), "--year", "2026"])
    assert result.exit_code == 0
    assert result.output.strip() == "ASVE-2026-0001"


def test_reserve_id_increments_existing(tmp_path):
    write_advisory(tmp_path, "ASVE-2026-0001")
    write_advisory(tmp_path, "ASVE-2026-0003")  # gaps allowed
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path), "--year", "2026"])
    assert result.exit_code == 0
    assert result.output.strip() == "ASVE-2026-0004"


def test_reserve_id_year_isolated(tmp_path):
    write_advisory(tmp_path, "ASVE-2025-0042")
    runner = CliRunner()
    result = runner.invoke(main, [str(tmp_path), "--year", "2026"])
    assert result.exit_code == 0
    assert result.output.strip() == "ASVE-2026-0001"
