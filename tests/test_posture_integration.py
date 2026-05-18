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


def test_posture_https_remote_endpoint_without_visible_auth_is_clean(tmp_path):
    manifest = {
        "mcpServers": {
            "remote": {
                "url": "https://example.com/mcp",
            }
        }
    }
    (tmp_path / "mcp.json").write_text(json.dumps(manifest))

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--fail-on",
            "none",
            "--include-posture",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "openaca-posture-missing-remote-auth" not in result.output
    assert "Posture findings" not in result.output


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


def test_collect_mcp_manifests_includes_claude_plugin_plugin_json(tmp_path):
    """.claude-plugin/plugin.json with inline mcpServers should be collected."""
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    plugin_data = {
        "name": "my-plugin",
        "mcpServers": {"remote": {"url": "https://example.com/mcp"}},
    }
    (plugin_dir / "plugin.json").write_text(json.dumps(plugin_data))

    results = collect_mcp_manifests([tmp_path])
    paths = [p for p, _ in results]
    assert any(p.name == "plugin.json" for p in paths)
    datas = [d for _, d in results]
    assert any(isinstance(d.get("mcpServers"), dict) for d in datas)


def test_collect_mcp_manifests_excludes_non_claude_plugin_json(tmp_path):
    """plugin.json NOT under .claude-plugin/ should not be collected."""
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "plugin.json").write_text('{"mcpServers": {"x": {"url": "https://x.example"}}}')

    results = collect_mcp_manifests([tmp_path])
    paths = [p for p, _ in results]
    assert not any(p.name == "plugin.json" for p in paths)


def test_posture_json_output_uses_unified_findings_array(tmp_path):
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
    assert "posture_findings" not in doc
    assert any(
        f["rule_id"] == "openaca-posture-mutable-install-reference"
        for f in doc["findings"]
        if f["finding_type"] == "posture"
    )
