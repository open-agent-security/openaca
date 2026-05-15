"""End-to-end smoke tests for the posture runner + --include-posture flag."""

from __future__ import annotations

import json

from click.testing import CliRunner

from tools.posture import collect_mcp_manifests
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


def test_collect_mcp_manifests_skips_dot_git_unconditionally(tmp_path):
    """`.git/` must be excluded even when include_gitignored=True (default)."""
    dot_git = tmp_path / ".git" / "refs"
    dot_git.mkdir(parents=True)
    (dot_git / "mcp.json").write_text('{"mcpServers": {"evil": {"url": "http://x"}}}')

    real = tmp_path / "mcp.json"
    real.write_text('{"mcpServers": {"ok": {"command": "npx", "args": ["-y", "pkg@1.0.0"]}}}')

    results = collect_mcp_manifests([tmp_path], include_gitignored=True)
    paths = [p for p, _ in results]
    assert real in paths
    assert not any(".git" in str(p) for p in paths)


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
