"""End-to-end smoke tests for the posture runner + --include-posture flag."""

from __future__ import annotations

import json

from click.testing import CliRunner

from tools.scan import main as scan_main


def test_posture_off_by_default():
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            "tests/fixtures/repos/sample-mcp",
            "--fail-on",
            "none",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Posture findings" not in result.output
    assert "openaca-posture-" not in result.output


def test_posture_on_emits_mutable_install():
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            "tests/fixtures/repos/sample-mcp",
            "--fail-on",
            "none",
            "--include-posture",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Posture findings" in result.output
    assert "openaca-posture-mutable-install-reference" in result.output
    # The fixture has an `sketchy-mcp` unpinned uvx entry but pinned others.
    assert "sketchy-mcp" in result.output


def test_posture_json_output_includes_array(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            "tests/fixtures/repos/sample-mcp",
            "--fail-on",
            "none",
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert "posture_findings" in doc
    assert any(
        f["rule_id"] == "openaca-posture-mutable-install-reference" for f in doc["posture_findings"]
    )
