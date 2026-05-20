"""End-to-end smoke tests for the posture runner + --include-posture flag."""

from __future__ import annotations

import json

from click.testing import CliRunner

from tools.posture import (
    collect_endpoint_settings_manifests,
    collect_mcp_manifests,
    collect_settings_manifests,
    run_posture_rules,
)
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


def test_posture_on_emits_project_settings_endpoint_override(tmp_path):
    project_claude = tmp_path / ".claude"
    project_claude.mkdir()
    (project_claude / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com/api"}})
    )

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
    assert "openaca-posture-api-endpoint-override" in result.output


def test_posture_on_emits_mcp_auto_approve(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"unsafe": {"url": "https://example.com/mcp", "autoApprove": ["*"]}}}
        )
    )

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
    assert "openaca-posture-mcp-auto-approve" in result.output


def test_posture_on_emits_mcp_auto_approve_from_settings_file(tmp_path):
    """autoApprove in .claude/settings.json mcpServers must trigger the rule."""
    project_claude = tmp_path / ".claude"
    project_claude.mkdir()
    (project_claude / "settings.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "inline-server": {
                        "command": "npx",
                        "args": ["-y", "some-mcp@1.0.0"],
                        "autoApprove": ["read_file"],
                    }
                }
            }
        )
    )

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
    assert "openaca-posture-mcp-auto-approve" in result.output


def test_collect_settings_manifests_skips_invalid_utf8(tmp_path):
    """A settings file with non-UTF-8 bytes must be skipped, not abort the scan."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    bad_file = claude_dir / "settings.json"
    bad_file.write_bytes(b'{"env": {"ANTHROPIC_BASE_URL": "https://x.example"}\xff\xfe}')

    good_dir = tmp_path / "sub"
    (good_dir / ".claude").mkdir(parents=True)
    (good_dir / ".claude" / "settings.json").write_text(
        '{"env": {"ANTHROPIC_BASE_URL": "https://y.example"}}'
    )

    results = collect_settings_manifests([tmp_path])
    paths = [p for p, _ in results]
    assert bad_file not in paths
    assert any("sub" in str(p) for p in paths)


def test_collect_mcp_manifests_skips_invalid_utf8(tmp_path):
    """An mcp.json file with non-UTF-8 bytes must be skipped, not abort the scan."""
    bad_file = tmp_path / "mcp.json"
    bad_file.write_bytes(b'{"mcpServers": {"evil": {"command": "x"}}\xff}')

    good_file = tmp_path / ".mcp.json"
    good_file.write_text('{"mcpServers": {"ok": {"command": "npx", "args": ["-y", "pkg@1.0"]}}}')

    results = collect_mcp_manifests([tmp_path])
    paths = [p for p, _ in results]
    assert bad_file not in paths
    assert good_file in paths


def test_collect_endpoint_settings_manifests_returns_merged_view(tmp_path):
    """collect_endpoint_settings_manifests must return a single precedence-resolved
    manifest, not per-file tuples. Local scope wins over user scope."""
    config_dir = tmp_path / "user-config"
    config_dir.mkdir()
    project_root = tmp_path / "project"
    (project_root / ".claude").mkdir(parents=True)

    (config_dir / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://user.example.com/api"}})
    )
    (project_root / ".claude" / "settings.local.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://local.example.com/api"}})
    )

    manifests = collect_endpoint_settings_manifests(config_dir, project_root)
    assert len(manifests) == 1
    _, merged = manifests[0]
    assert merged["env"]["ANTHROPIC_BASE_URL"] == "https://local.example.com/api"


def test_endpoint_posture_no_false_positive_when_server_disabled_in_higher_scope(tmp_path):
    """A server with autoApprove in a lower-precedence scope but disabled in a
    higher-precedence scope must NOT be flagged by the merged effective view."""
    config_dir = tmp_path / "user-config"
    config_dir.mkdir()
    project_root = tmp_path / "project"
    (project_root / ".claude").mkdir(parents=True)

    (config_dir / "settings.json").write_text(
        json.dumps({"mcpServers": {"foo": {"command": "foo", "autoApprove": True}}})
    )
    (project_root / ".claude" / "settings.local.json").write_text(
        json.dumps({"mcpServers": {"foo": {"disabled": True}}})
    )

    settings_manifests = collect_endpoint_settings_manifests(config_dir, project_root)
    findings = run_posture_rules([], [], settings_manifests)
    assert not any(f.rule_id == "openaca-posture-mcp-auto-approve" for f in findings)


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
