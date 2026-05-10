import json
from pathlib import Path

from click.testing import CliRunner

from tools.scan import _esc_data, _esc_param, main

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def test_scan_finds_exposed_mcp(tmp_path):
    """Scan picks up the @cyanheads/git-mcp-server@1.1.0 in package.json
    and matches ASVE-2026-0001 (fixed in 1.2.3)."""
    sarif_out = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "--sarif",
            str(sarif_out),
        ],
    )
    assert result.exit_code == 1, result.output
    sarif = json.loads(sarif_out.read_text())
    rule_ids = {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
    assert "ASVE-2026-0001" in rule_ids


def test_scan_clean_repo_exits_zero(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "package.json").write_text('{"name":"clean","version":"0","dependencies":{}}')
    sarif_out = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(clean),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "--sarif",
            str(sarif_out),
        ],
    )
    assert result.exit_code == 0, result.output


def test_scan_emits_github_annotation_lines(tmp_path):
    """The annotation lines must use ::error::/::warning:: format and reference
    the manifest path so PR reviewers see findings inline."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code == 1
    annotations = [line for line in result.output.splitlines() if line.startswith("::")]
    assert annotations
    assert any("ASVE-2026-0001" in line for line in annotations)
    assert any("file=" in line and "package.json" in line for line in annotations)


def test_scan_fail_on_high_only_exits_zero_for_low_or_unknown(tmp_path):
    """`--fail-on high` should exit 0 when findings are all low/unknown
    confidence — useful for consumers that only want to block PRs on
    concrete-version vulnerabilities."""
    target = tmp_path / "loose"
    target.mkdir()
    (target / "package.json").write_text(
        '{"name":"loose","version":"0","dependencies":{"@cyanheads/git-mcp-server":"^1.0.0"}}'
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(target),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "--fail-on",
            "high",
        ],
    )
    assert result.exit_code == 0, result.output


def test_scan_fail_on_none_always_exits_zero(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "--fail-on",
            "none",
        ],
    )
    assert result.exit_code == 0, result.output


def test_scan_default_output_reports_manifest_and_component_counts(tmp_path):
    """Default output should always tell the user what was scanned, even
    when there are no findings — bare 'no findings' leaves users wondering
    if the scanner looked at anything at all."""
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "package.json").write_text(
        '{"name":"clean","version":"0","dependencies":{"left-pad":"1.3.0"}}'
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--target", str(clean), "--advisories", str(REPO_ROOT / "advisories")],
    )
    assert result.exit_code == 0
    # CliRunner mixes stdout+stderr into result.output.
    assert "scanned 1 manifest(s)" in result.output
    assert "1 component(s)" in result.output
    assert "no findings" in result.output


def test_scan_reports_parse_failure_not_no_manifests(tmp_path):
    """A target containing only malformed manifests must not report 'no manifests
    found' — that would hide the scan blind spot from the user."""
    (tmp_path / "package.json").write_text("{invalid json !!!")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--target", str(tmp_path), "--advisories", str(REPO_ROOT / "advisories")],
    )
    assert result.exit_code == 0
    assert "no manifests found" not in result.output
    assert "none parsed successfully" in result.output


def test_scan_partial_parse_failures_noted_in_summary(tmp_path):
    """When some manifests parse and some don't, the summary must report the
    total found count and flag how many failed — hiding partial failures gives
    false confidence in scan coverage."""
    (tmp_path / "package.json").write_text(
        '{"name":"ok","version":"0","dependencies":{"left-pad":"1.3.0"}}'
    )
    (tmp_path / "mcp.json").write_text("{invalid json !!!")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--target", str(tmp_path), "--advisories", str(REPO_ROOT / "advisories")],
    )
    assert result.exit_code == 0
    assert "scanned 2 manifest(s)" in result.output
    assert "1 failed to parse" in result.output


def test_scan_default_output_reports_no_manifests_when_target_is_empty(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--target", str(tmp_path), "--advisories", str(REPO_ROOT / "advisories")],
    )
    assert result.exit_code == 0
    assert "no manifests found" in result.output


def test_scan_verbose_lists_each_manifest_and_matched_component(tmp_path):
    """`-v` should enumerate every manifest scanned and every matched
    component → advisory pairing, so users can see what the scanner
    actually inspected."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "loaded" in result.output and "advisory(ies)" in result.output
    assert "package.json" in result.output
    assert "matched" in result.output and "finding(s):" in result.output
    assert "ASVE-2026-0001" in result.output
    assert "(high)" in result.output


def test_scan_verbose_clean_repo_still_lists_manifests(tmp_path):
    """Verbose mode against a clean repo should still show what was
    scanned — that's the whole point of verbose."""
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "package.json").write_text('{"name":"clean","version":"0","dependencies":{}}')
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(clean),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0
    assert "scanned 1 manifest(s)" in result.output
    assert "package.json" in result.output


def test_esc_param_encodes_workflow_metacharacters():
    """Commas, colons, percent, and newlines in parameter values must be encoded
    so the GitHub workflow command parser doesn't misread key=value pairs."""
    assert _esc_param("path/to,file") == "path/to%2Cfile"
    assert _esc_param("path:to") == "path%3Ato"
    assert _esc_param("100%") == "100%25"
    assert _esc_param("line\r\nbreak") == "line%0D%0Abreak"
    assert _esc_param("normal/path/file.json") == "normal/path/file.json"


def test_esc_data_encodes_message_metacharacters():
    """Percent, CR, and LF in annotation messages must be encoded; colons/commas
    are safe in the data portion and must pass through unchanged."""
    assert _esc_data("100%") == "100%25"
    assert _esc_data("line\r\nbreak") == "line%0D%0Abreak"
    assert _esc_data("colon:comma,safe") == "colon:comma,safe"
    assert _esc_data("plain message") == "plain message"


# Plan 007: subcommand split tests. Existing tests above call `main` with the
# legacy flag set (no subcommand) and exercise the back-compat default to repo.


def test_repo_subcommand_explicit():
    """Explicit `asve-scan repo` runs the same logic as the back-compat default."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code == 1
    assert "ASVE-2026-0001" in result.output


def test_no_subcommand_back_compat_invokes_repo():
    """`asve-scan --target ... --advisories ...` with no subcommand still works
    because the GitHub Action and existing scripts depend on this surface."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code == 1
    assert "ASVE-2026-0001" in result.output


def test_fs_subcommand_minimal_install_no_findings():
    """fs mode against the minimal fixture install resolves the active plugin
    and reports no findings (V0 corpus has no plugin advisories yet)."""
    install_root = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(install_root),
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code == 0
    assert "resolved 1 active plugin(s)" in result.output
    assert "no findings" in result.output


def test_fs_subcommand_matches_claude_plugin_advisory(tmp_path):
    """fs mode + a claude-plugin-ecosystem advisory + the minimal install
    fires a finding via the matcher's existing version-range path."""
    install_root = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    advisories_dir = tmp_path / "advisories"
    advisories_dir.mkdir()
    (advisories_dir / "ASVE-2026-9999.yaml").write_text(
        """\
schema_version: 1.7.5
id: ASVE-2026-9999
type: vulnerability
summary: test plugin advisory for plan 007
modified: '2026-05-09T00:00:00Z'
affected:
- package:
    ecosystem: claude-plugin
    name: sample-plugin
  ranges:
  - type: ECOSYSTEM
    events:
    - introduced: '0'
    - fixed: '2.0.0'
"""
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(install_root),
            "--advisories",
            str(advisories_dir),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "ASVE-2026-9999" in result.output


def test_fs_subcommand_verbose_lists_resolved_plugins():
    install_root = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(install_root),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0
    assert "claude-plugin/sample-plugin@1.2.0" in result.output
    assert "deadbeef" in result.output  # gitCommitSha shortened


def test_fs_subcommand_project_root_uses_user_install_root(tmp_path, monkeypatch):
    """When --target is a project repo with .claude/settings.json but NO
    plugins/installed_plugins.json, the resolver routes to the user's
    install root (~/.claude). Stub Path.home() to a clean tmp dir so the
    test doesn't pick up the real machine's installed plugins."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    fake_install = fake_home / ".claude"
    fake_install.mkdir()
    # User-scope settings exist but enable no plugins.
    (fake_install / "settings.json").write_text("{}")

    project = tmp_path / "myproj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text("{}")

    monkeypatch.setattr("tools.scan.Path.home", lambda: fake_home)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(project),
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code == 0
    assert "resolved 0 active plugin(s)" in result.output
