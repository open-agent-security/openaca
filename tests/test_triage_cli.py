import json

from click.testing import CliRunner

from tools.cli import main as openaca_main


def _write_scan_json(tmp_path):
    path = tmp_path / "scan.json"
    path.write_text(
        json.dumps(
            {
                "target": {"host_surface": "repository", "rows": [{"label": "path", "value": "."}]},
                "stats": {"components": 2},
                "findings": [
                    {
                        "finding_type": "vulnerability",
                        "id": "GHSA-test",
                        "title": "Package vulnerability",
                        "severity": "HIGH",
                        "confidence": "high",
                        "fixed_in": "2.0.0",
                        "component": {"type": "package", "name": "lodash"},
                        "component_path": [
                            {"type": "plugin", "name": "demo"},
                            {"type": "package", "name": "lodash"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_openaca_triage_defaults_to_text(tmp_path):
    scan_json = _write_scan_json(tmp_path)

    result = CliRunner().invoke(openaca_main, ["triage", str(scan_json), "--report", "exposure"])

    assert result.exit_code == 0, result.output
    assert "Exposure report" in result.output
    assert "HIGH - demo" in result.output


def test_openaca_triage_writes_markdown(tmp_path):
    scan_json = _write_scan_json(tmp_path)
    report_path = tmp_path / "report.md"

    result = CliRunner().invoke(
        openaca_main,
        [
            "triage",
            str(scan_json),
            "--report",
            "exposure",
            "--format",
            "markdown",
            "--output",
            str(report_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "# OpenACA Exposure Report" in report_path.read_text(encoding="utf-8")
    assert result.output == ""


def test_openaca_triage_emits_json(tmp_path):
    scan_json = _write_scan_json(tmp_path)

    result = CliRunner().invoke(
        openaca_main,
        ["triage", str(scan_json), "--report", "exposure", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["cards"][0]["action"] == "upgrade"


def test_openaca_triage_rejects_malformed_scan_json(tmp_path):
    scan_json = tmp_path / "scan.json"
    scan_json.write_text("{", encoding="utf-8")

    result = CliRunner().invoke(openaca_main, ["triage", str(scan_json)])

    assert result.exit_code != 0
    assert "invalid JSON" in result.output
