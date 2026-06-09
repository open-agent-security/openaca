import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from tools.render import _esc_data, _esc_param
from tools.scan import main

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def _mark_as_plugin(root: Path, name: str = "test-plugin", version: str = "1.0.0") -> None:
    """Write `.claude-plugin/plugin.json` to mark `root` as a plugin repo.

    Under V0 agent-composition scope, dep manifests (package.json,
    pyproject.toml, package-lock.json, uv.lock) are classified as
    "software-dependency" and suppressed unless co-located with this
    marker — at which point they become "agent-dependency" and surface
    in scan output. Tests that build dep manifests in tmp_path and
    expect findings need this helper.
    """
    plugin_dir = root / ".claude-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )


def test_scan_finds_exposed_mcp(tmp_path):
    """Scan picks up the @cyanheads/git-mcp-server@1.1.0 in package.json
    and matches GHSA-3q26-f695-pp76 (fixed in 1.2.3)."""
    sarif_out = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--sarif",
            str(sarif_out),
        ],
    )
    assert result.exit_code == 1, result.output
    sarif = json.loads(sarif_out.read_text())
    rule_ids = {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
    assert "GHSA-3q26-f695-pp76" in rule_ids


def test_scan_clean_repo_exits_zero(tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "package.json").write_text('{"name":"clean","version":"0","dependencies":{}}')
    sarif_out = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(clean),
            "--sarif",
            str(sarif_out),
        ],
    )
    assert result.exit_code == 0, result.output


def test_scan_emits_github_annotation_lines(tmp_path):
    """The annotation lines must use ::error::/::warning:: format and reference
    the manifest path so PR reviewers see findings inline. Format moved to a
    dedicated --format github mode; the content didn't change."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--format",
            "github",
        ],
    )
    assert result.exit_code == 1
    annotations = [line for line in result.output.splitlines() if line.startswith("::")]
    assert annotations
    assert any("GHSA-3q26-f695-pp76" in line for line in annotations)
    assert any("file=" in line and "package.json" in line for line in annotations)


def test_scan_fail_on_high_only_exits_zero_for_low_or_unknown(tmp_path):
    """`--fail-on high` should exit 0 when findings are all low/unknown
    confidence — useful for consumers that only want to block PRs on
    concrete-version vulnerabilities."""
    target = tmp_path / "loose"
    target.mkdir()
    _mark_as_plugin(target, name="loose")
    (target / "package.json").write_text(
        '{"name":"loose","version":"0","dependencies":{"@cyanheads/git-mcp-server":"^1.0.0"}}'
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(target),
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
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
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
    _mark_as_plugin(clean, name="clean")
    (clean / "package.json").write_text(
        '{"name":"clean","version":"0","dependencies":{"left-pad":"1.3.0"}}'
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(clean),
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 0
    # Text format footer reports the totals. The fixture has both a
    # plugin.json (self-identity ref) and a package.json (one dep), so
    # two manifests and two components.
    assert "Scanned 2 manifests" in result.output
    assert "2 components" in result.output
    assert "advisories: 0" in result.output


def test_scan_reports_parse_failure_not_no_manifests(tmp_path):
    """A target containing only malformed manifests must surface the parse
    failure rather than silently reporting no findings on zero manifests."""
    (tmp_path / "package.json").write_text("{invalid json !!!")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 0
    # The text-format footer reflects the parse failure: 1 manifest found,
    # 0 components, with the failure note.
    assert "Scanned 1 manifest" in result.output
    assert "failed to parse" in result.output


def test_scan_partial_parse_failures_noted_in_summary(tmp_path):
    """When some manifests parse and some don't, the footer must report the
    total found count and flag how many failed — hiding partial failures gives
    false confidence in scan coverage."""
    (tmp_path / "package.json").write_text(
        '{"name":"ok","version":"0","dependencies":{"left-pad":"1.3.0"}}'
    )
    (tmp_path / "mcp.json").write_text("{invalid json !!!")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 0
    assert "Scanned 2 manifests" in result.output
    assert "1 failed to parse" in result.output


def test_scan_default_output_reports_no_manifests_when_target_is_empty(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 0
    # No manifests visited → 0/0 summary with zero advisories.
    assert "Scanned 0 manifests" in result.output
    assert "advisories: 0" in result.output


def test_repo_default_output_is_inventory_card_with_findings():
    """Default (non-verbose) repo text output is the inventory-first card:
    Target block, the inventory tree, finding IDs, and the Summary line — all
    on stdout, so first-run reads as 'understands my stack', not '0 CVEs'."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["repo", "--target", str(FIXTURES / "repos" / "exposed-mcp"), "--no-color"],
    )
    assert result.exit_code == 1, result.output
    # Card sections present.
    assert "host surface: repository" in result.output
    assert "Inventory" in result.output
    assert "Summary" in result.output
    # Inventory tree shows the bundled component, flagged with its advisory id.
    assert "@cyanheads/git-mcp-server" in result.output
    assert "GHSA-3q26-f695-pp76" in result.output
    # Summary recaps the advisory count; Next guides the user onward.
    assert "advisories: 1" in result.output
    assert "openaca bom repo --target" in result.output


def test_scan_verbose_lists_each_manifest_and_matched_component(tmp_path):
    """`-v` should enumerate every manifest scanned and every matched
    component → advisory pairing, so users can see what the scanner
    actually inspected."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "-v",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "loaded" in result.output and "OpenACA overlay(s)" in result.output
    assert "package.json" in result.output
    assert "matched" in result.output and "finding(s):" in result.output
    assert "GHSA-3q26-f695-pp76" in result.output
    assert "(high)" in result.output


def test_scan_verbose_clean_repo_still_lists_manifests(tmp_path):
    """Verbose mode against a clean repo should still show what was scanned.

    For text output the manifest count lives in the card Summary (stdout) and
    `-v` adds the overlay/federation diagnostics on stderr; the old
    `scanned N manifest(s):` stderr enumeration was removed to avoid
    duplicating the stdout inventory card."""
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "package.json").write_text('{"name":"clean","version":"0","dependencies":{}}')
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(clean),
            "-v",
        ],
    )
    assert result.exit_code == 0
    # Count is in the card Summary; verbose still emits the overlay diagnostic.
    assert "Scanned 1 manifest" in result.output
    assert "OpenACA overlay(s)" in result.output


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


# Plan 007: subcommand split tests. OpenACA is pre-launch, so a subcommand is
# required rather than preserving a no-subcommand compatibility shim.


def test_repo_subcommand_explicit():
    """Explicit `openaca scan repo` scans repository manifests."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
        ],
    )
    assert result.exit_code == 1
    assert "GHSA-3q26-f695-pp76" in result.output


def test_no_subcommand_fails_with_usage():
    """Invoking `openaca scan` without a subcommand should exit non-zero with
    Click's standard usage error. There is no back-compat fallback."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
        ],
    )
    assert result.exit_code != 0
    assert "no such option" in result.output.lower() or "missing command" in result.output.lower()


def test_endpoint_subcommand_minimal_install_no_findings():
    """endpoint mode against the minimal fixture install resolves the active plugin
    and reports no findings (V0 corpus has no plugin advisories yet)."""
    config_dir = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(config_dir),
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 0
    assert "Scanned 1 active plugin" in result.output
    assert "advisories: 0" in result.output


def test_endpoint_subcommand_treats_plugin_graph_identity_as_inventory_only(tmp_path):
    """Endpoint mode inventories plugin graph identity but does not match on it."""
    config_dir = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    advisories_dir = tmp_path / "advisories"
    advisories_dir.mkdir()
    (advisories_dir / "CVE-2026-9999.yaml").write_text(
        """\
schema_version: 1.7.5
id: CVE-2026-9999
type: vulnerability
summary: test plugin advisory for plan 007
modified: '2026-05-09T00:00:00Z'
database_specific:
  openaca:
    component_identity: plugin/test-marketplace/sample-plugin
"""
    )
    runner = CliRunner()
    from unittest.mock import patch

    advisory = yaml.safe_load((advisories_dir / "CVE-2026-9999.yaml").read_text())

    with patch("tools.scan._load_osv_with_overlays", lambda refs: ([advisory], [], 0, {})):
        result = runner.invoke(
            main,
            [
                "endpoint",
                "--config-dir",
                str(config_dir),
            ],
        )
    assert result.exit_code == 0, result.output
    assert "plugin/test-marketplace/sample-plugin@1.2.0" in result.output
    assert "No advisories matched" in result.output


def test_endpoint_posture_ignores_uninstalled_plugin_manifests(tmp_path):
    active_dir = tmp_path / "plugins" / "cache" / "official" / "active" / "1.0.0"
    active_dir.mkdir(parents=True)
    (active_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"active": {"url": "http://active.example/mcp"}}})
    )
    inactive_dir = (
        tmp_path / "plugins" / "marketplaces" / "official" / "external_plugins" / "inactive"
    )
    inactive_dir.mkdir(parents=True)
    (inactive_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"inactive": {"url": "http://inactive.example/mcp"}}})
    )
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"active@official": True}})
    )
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "active@official": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(active_dir)}
                    ]
                },
            }
        )
    )

    runner = CliRunner()
    from unittest.mock import patch

    with patch("tools.scan._load_osv_with_overlays", lambda refs: ([], [], 0, {})):
        result = runner.invoke(
            main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
                "--include-posture",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "mcp-server/active @ http://active.example/mcp" in result.output
    assert "mcp-server/inactive @ http://inactive.example/mcp" not in result.output


def test_endpoint_posture_flags_unversioned_active_plugin(tmp_path):
    cache_dir = tmp_path / "plugins" / "cache" / "official" / "feature-dev" / "unknown"
    cache_dir.mkdir(parents=True)
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"feature-dev@official": True}})
    )
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "feature-dev@official": [{"scope": "user", "installPath": str(cache_dir)}]
                },
            }
        )
    )

    runner = CliRunner()
    from unittest.mock import patch

    with patch("tools.scan._load_osv_with_overlays", lambda refs: ([], [], 0, {})):
        result = runner.invoke(
            main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
                "--include-posture",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "openaca-posture-mutable-install-reference" in result.output
    assert "plugin/official/feature-dev@unknown" in result.output


def test_endpoint_subcommand_verbose_lists_resolved_plugins():
    config_dir = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(config_dir),
            "-v",
        ],
    )
    assert result.exit_code == 0
    assert "plugin/test-marketplace/sample-plugin@1.2.0" in result.output
    assert "deadbeef" in result.output  # gitCommitSha shortened


def test_endpoint_verbose_non_string_git_commit_sha_does_not_crash(monkeypatch):
    """gitCommitSha from installed_plugins.json is user-editable; a non-string
    value (e.g. integer) must not crash verbose endpoint output."""
    from tools.component_ref import ComponentRef

    fake_ref = ComponentRef(
        name="bad-sha-plugin",
        version="1.0.0",
        component_identity="plugin/bad-sha-plugin",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.bad-sha-plugin@test[0]",
        attributed_to=None,
        extra={
            "component_type": "plugin",
            "gitCommitSha": 123,
            "scope": "user",
            "installPath": None,
            "marketplace": "test",
        },
    )
    monkeypatch.setattr("tools.scan.parse_install", lambda **_kwargs: ([fake_ref], []))

    config_dir = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(config_dir),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "sha:" not in result.output


def test_endpoint_subcommand_project_layers_with_config_dir(tmp_path):
    """--project adds project settings context on top of --config-dir."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    config_dir = fake_home / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}")

    project = tmp_path / "myproj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text("{}")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(config_dir),
            "--project",
            str(project),
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 0
    assert "Scanned 0 active plugins" in result.output


def test_endpoint_subcommand_project_root_detected_via_local_settings_only(tmp_path):
    """A project that only ships `.claude/settings.local.json` can still be
    supplied as endpoint project context."""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    config_dir = fake_home / ".claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}")

    project = tmp_path / "local-only-proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.local.json").write_text("{}")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(config_dir),
            "--project",
            str(project),
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 0
    # Verbose output would say which install root was picked; the smoke check
    # is that resolution succeeds and the target is not misclassified.
    assert "Scanned 0 active plugins" in result.output


def test_endpoint_defaults_to_claude_config_dir_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}")
    (config_dir / "plugins").mkdir()
    (config_dir / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"config_dir={config_dir}" in result.output
    assert "mode=endpoint" in result.output


def test_endpoint_defaults_to_home_claude_when_env_missing(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".claude"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text("{}")
    (config_dir / "plugins").mkdir()
    (config_dir / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("tools.scan.Path.home", lambda: fake_home)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"config_dir={config_dir}" in result.output
    assert "mode=endpoint" in result.output


def test_endpoint_omits_project_by_default_and_emits_note(tmp_path, monkeypatch):
    """Without --project, the endpoint scan does NOT include project
    context, and emits an unconditional note telling the tester how to
    add it.

    The note is unconditional — no cwd-has-Claude-markers detection.
    The goal is for testers to discover the flag on their first
    endpoint scan, not for the scanner to be clever about when to
    surface it.
    """
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".claude"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text("{}")

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("tools.scan.Path.home", lambda: fake_home)

    runner = CliRunner()
    result = runner.invoke(main, ["endpoint"])

    assert result.exit_code == 0, result.output
    # Default text card: project shown as not included; the "add --project"
    # guidance is a Next action (the legacy stderr note is verbose/non-text only).
    assert "project: not included" in result.output
    assert "include project-local config" in result.output
    assert "--project" in result.output


def test_endpoint_explicit_project_suppresses_the_note(tmp_path, monkeypatch):
    """When --project is provided, the educational note is suppressed —
    the user has made an explicit choice and doesn't need to be told
    how to add project context."""
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".claude"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text("{}")

    project = tmp_path / "myproj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text("{}")

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("tools.scan.Path.home", lambda: fake_home)

    runner = CliRunner()
    result = runner.invoke(main, ["endpoint", "--project", str(project)])

    assert result.exit_code == 0, result.output
    assert f"project: {project}" in result.output
    # --project given → no "add project context" Next action.
    assert "include project-local config" not in result.output


def test_endpoint_scan_scope_visible_in_default_card(tmp_path, monkeypatch):
    """Scan scope is never hidden ("transparency, not surprise"). For default
    text output the card Target block shows the config dir and project context;
    the legacy stderr `detected config_dir=...` line is emitted only with `-v`
    or for machine formats (so it doesn't precede/duplicate the card)."""
    fake_home = tmp_path / "home"
    config_dir = fake_home / ".claude"
    config_dir.mkdir(parents=True)
    (config_dir / "settings.json").write_text("{}")

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("tools.scan.Path.home", lambda: fake_home)

    runner = CliRunner()
    # Default (non-verbose) text: scope is in the card Target block.
    result = runner.invoke(main, ["endpoint"])
    assert result.exit_code == 0, result.output
    assert "host surface: Claude Code" in result.output
    assert f"config: {config_dir}" in result.output
    assert "project: not included" in result.output
    # The legacy stderr preamble is not shown for default text.
    assert "detected config_dir=" not in result.output

    # -v still emits the stderr diagnostic line.
    result_v = runner.invoke(main, ["endpoint", "-v"])
    assert result_v.exit_code == 0, result_v.output
    assert f"detected config_dir={config_dir}" in result_v.output
    assert "mode=endpoint" in result_v.output


def test_fs_subcommand_is_not_kept_as_alias():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"),
        ],
    )
    assert result.exit_code != 0
    assert "no such command" in result.output.lower()


# Plan 007 follow-up: group-level option forwarding to subcommands.
# Placing shared options before the subcommand name must behave identically
# to placing them after it (subcommand-explicit always wins on conflict).


def test_group_fail_on_none_forwards_to_repo_subcommand():
    """--fail-on none before the subcommand is honored, not silently dropped."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--fail-on",
            "none",
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
        ],
    )
    assert result.exit_code == 0, result.output  # findings exist but --fail-on none → exit 0


def test_group_sarif_forwards_to_repo_subcommand(tmp_path):
    """--sarif before the subcommand is honored and the file is written."""
    sarif_out = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--sarif",
            str(sarif_out),
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
        ],
    )
    assert sarif_out.exists(), f"SARIF not written; exit {result.exit_code}: {result.output}"


def test_group_verbose_forwards_to_repo_subcommand():
    """-v before the subcommand is honored and verbose output appears."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "-v",
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
        ],
    )
    assert "loaded" in result.output and "OpenACA overlay(s)" in result.output


def test_subcommand_fail_on_takes_precedence_over_group():
    """Subcommand-explicit --fail-on beats the group-level value."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--fail-on",
            "none",  # group level: would exit 0
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--fail-on",
            "any",  # subcommand level: overrides → exit 1
        ],
    )
    assert result.exit_code == 1, result.output


def test_endpoint_subcommand_includes_transitive_by_default(tmp_path):
    """Endpoint mode emits Tier-2 lockfile refs in addition to Tier-1."""
    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [{"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}]
                },
            }
        )
    )
    # Use the resolver directly to inspect refs (CLI suppresses non-matching
    # refs in its summary — the dispatch-level test is cleaner).
    from tools.parsers.claude_install import parse_install

    refs, _ = parse_install(install_root=tmp_path)
    assert any(r.ecosystem == "npm" and r.name == "lodash" for r in refs)


def test_endpoint_subcommand_queries_osv_by_default(tmp_path):
    """OSV augmentation is always invoked for versioned agent refs."""
    from unittest.mock import patch

    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [{"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}]
                },
            }
        )
    )

    fake_advisory = {
        "schema_version": "1.7.1",
        "id": "GHSA-FAKE-LODASH",
        "modified": "2026-05-10T00:00:00Z",
        "type": "vulnerability",
        "published": "2026-05-10T00:00:00Z",
        "summary": "test",
        "details": "test",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "lodash"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "5.0.0"}]}
                ],
            }
        ],
    }

    def fake_augment(refs, base_corpus):
        return list(base_corpus) + [fake_advisory], []

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
                "-v",
            ],
        )
    assert result.exit_code == 1, result.output  # finding crossed default --fail-on=any
    assert "GHSA-FAKE-LODASH" in result.output


def test_endpoint_subcommand_uses_osv_and_bundled_overlays_by_default(tmp_path):
    """Overlay-only V0 has no local matchable advisory DB. Scans query OSV for
    versioned agent refs by default, then apply bundled OpenACA agent-context
    overlays by alias."""
    from unittest.mock import patch

    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [{"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}]
                },
            }
        )
    )

    fake_advisory = {
        "schema_version": "1.7.5",
        "id": "GHSA-3q26-f695-pp76",
        "aliases": ["CVE-2025-53107"],
        "modified": "2026-05-10T00:00:00Z",
        "type": "vulnerability",
        "published": "2026-05-10T00:00:00Z",
        "summary": "@cyanheads/git-mcp-server command injection",
        "details": "test",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "@cyanheads/git-mcp-server"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.1.5"}]}
                ],
            }
        ],
    }

    def fake_augment(refs, base_corpus):
        return list(base_corpus) + [fake_advisory], []

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
                "--format",
                "text",
                "-v",
            ],
        )

    assert result.exit_code == 1, result.output
    assert "GHSA-3q26-f695-pp76" in result.output
    assert "taxonomies: owasp_agentic_top10=asi02,asi05" in result.output
    assert "evidence_level: confirmed" in result.output


def test_endpoint_subcommand_verbose_lists_queried_purls_and_skips(tmp_path):
    """Verbose output surfaces queried OSV targets and skipped refs."""
    from unittest.mock import patch

    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [{"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}]
                },
            }
        )
    )

    def fake_augment(refs, base_corpus):
        return list(base_corpus), []

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
                "-v",
            ],
        )
    assert result.exit_code == 0, result.output
    # The lodash dep should appear as a queried OSV target.
    assert (
        "federation: queried 1 target(s) on osv.dev; fetched 0 advisory record(s)" in result.output
    )
    assert "pkg:npm/lodash@4.17.20" in result.output
    # The source-less plugin self-identity ref should be in the skip count
    assert "plugin=1" in result.output


def test_repo_subcommand_verbose_lists_queried_purls(tmp_path):
    """Same verbose surface in repo mode (parity with endpoint mode)."""
    from unittest.mock import patch

    _mark_as_plugin(tmp_path, name="demo")
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0", "dependencies": {"lodash": "4.17.20"}})
    )

    def fake_augment(refs, base_corpus):
        return list(base_corpus), []

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "repo",
                "--target",
                str(tmp_path),
                "-v",
            ],
        )
    assert result.exit_code == 0, result.output
    assert (
        "federation: queried 1 target(s) on osv.dev; fetched 0 advisory record(s)" in result.output
    )
    assert "loaded 0 OSV advisory record(s)" not in result.output


def test_repo_subcommand_verbose_renders_inventory_tree(tmp_path):
    """Repo verbose output should explain composition with the same tree shape
    endpoint mode uses, not just a flat manifest count list."""
    from unittest.mock import patch

    _mark_as_plugin(tmp_path, name="demo-plugin")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "dependencies": {"lodash": "4.17.20"},
            }
        )
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "git": {
                        "command": "npx",
                        "args": ["@cyanheads/git-mcp-server@1.1.0"],
                    }
                }
            }
        )
    )
    skill_dir = tmp_path / "skills" / "audit"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: audit\ndescription: Audit agent configuration.\n---\n\n# Audit\n"
    )

    def fake_augment(refs, base_corpus):
        return list(base_corpus), []

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "repo",
                "--target",
                str(tmp_path),
                "-v",
            ],
        )

    assert result.exit_code == 0, result.output
    assert f"repo {tmp_path}" in result.output
    assert "plugin/demo-plugin@1.0.0" in result.output
    assert "package deps/ (1)" in result.output
    assert "lodash@4.17.20" in result.output
    assert "skills/ (1)" in result.output
    assert "audit" in result.output
    assert "MCPs/ (1)" in result.output
    assert "@cyanheads/git-mcp-server@1.1.0" in result.output


def test_endpoint_subcommand_federate_osv_verbose_no_queryable_refs(tmp_path):
    """When nothing has a queryable OSV target (e.g., only source-less plugin refs),
    verbose says so explicitly rather than emitting an empty list."""
    from unittest.mock import patch

    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [{"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}]
                },
            }
        )
    )

    def fake_augment(refs, base_corpus):
        return list(base_corpus), []

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
                "-v",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "no queryable OSV.dev targets" in result.output


def test_endpoint_subcommand_federate_osv_failure_prints_warning(tmp_path, capfd):
    """OSV.dev network failure prints unconditional stderr warning even
    without -v. Exit code stays findings-driven (= 0 when no findings)."""
    from unittest.mock import patch

    (tmp_path / "settings.json").write_text(json.dumps({}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )

    def fake_augment(refs, base_corpus):
        return list(base_corpus), ["osv.dev federation failed: connection refused"]

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
            ],
        )
    assert result.exit_code == 0
    assert "osv.dev federation failed" in result.output


def test_endpoint_verbose_shows_per_plugin_tier2_coverage(tmp_path):
    """Verbose output includes a 'npm: package-lock.json (transitive, N packages)'
    line per plugin that has Tier-2 coverage."""
    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                    "node_modules/underscore": {"version": "1.13.0"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [{"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}]
                },
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    # Tier-2 coverage now renders as a tree leaf under the plugin block.
    assert "package-lock.json" in result.output
    assert "2 transitive" in result.output
    assert "npm/ deps" in result.output


def test_endpoint_verbose_shows_manifest_fallback_line(tmp_path):
    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"lodash": "^4.17.0"}})
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [{"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}]
                },
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "-v",
        ],
    )
    assert "direct only" in result.output
    assert "package.json" in result.output
    assert "npm/ deps" in result.output


def test_bundled_breakdown_excludes_tier2_lockfile_refs(tmp_path):
    """A plugin with 1 Tier-1 bundled MCP (from .mcp.json) + multiple
    Tier-2 lockfile npm deps should show '1 bundled MCPs' in verbose output,
    not inflated counts."""
    cache_dir = tmp_path / "cache" / "demo" / "1.0.0"
    cache_dir.mkdir(parents=True)
    # Tier-1: a default .mcp.json with one bundled MCP server.
    (cache_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"foo": {"command": "npx", "args": ["-y", "@org/foo@1.0.0"]}}})
    )
    # Tier-2: a package-lock.json with multiple transitive deps.
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                    "node_modules/underscore": {"version": "1.13.0"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@m": [{"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}]
                },
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    # The tree's MCPs/ category counts only the Tier-1 .mcp.json ref (1),
    # NOT 3 (1 Tier-1 + 2 Tier-2 lockfile deps). Tier-2 aggregates separately.
    assert "MCPs/ (1)" in result.output
    # The Tier-2 aggregate line appears as its own tree leaf.
    assert "2 transitive" in result.output


def test_endpoint_verbose_lists_direct_skills_individually(tmp_path):
    """The 'direct components: N skills' summary line should be followed by
    one indented line per direct skill identity, so users can see exactly
    what was inventoried — mirroring the per-plugin breakdown."""
    skills_root = tmp_path / "skills"
    for name in ("zebra-skill", "alpha-skill", "middle-skill"):
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\nbody\n")
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    # Tree renders a `direct components/` root with a `skills/ (3)` branch.
    assert "direct components/" in result.output
    assert "skills/ (3)" in result.output
    # Each skill name appears as a leaf, sorted alphabetically. The tree
    # strips the `skill/` ecosystem prefix from leaf labels (the
    # parent category line already states the kind).
    alpha_idx = result.output.find("alpha-skill")
    middle_idx = result.output.find("middle-skill")
    zebra_idx = result.output.find("zebra-skill")
    assert alpha_idx >= 0 and middle_idx >= 0 and zebra_idx >= 0
    assert alpha_idx < middle_idx < zebra_idx


def test_endpoint_verbose_omits_direct_listing_when_no_direct_components(tmp_path):
    """No direct components → no summary line and no per-component list."""
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "-v",
        ],
    )
    assert result.exit_code == 0
    assert "direct components:" not in result.output


def test_repo_subcommand_skips_gitignored_by_default(tmp_path):
    """End-to-end: a host package.json declares no vulnerable dep, but a
    gitignored node_modules/lodash/package.json contains a vulnerable shape.
    Without the flag, the gitignored file is skipped → exit 0. With
    --include-gitignored, it gets walked."""
    _mark_as_plugin(tmp_path, name="host")
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "host", "version": "1.0.0", "dependencies": {}})
    )
    nm_dir = tmp_path / "node_modules" / "@cyanheads" / "git-mcp-server"
    nm_dir.mkdir(parents=True)
    # The vendored package.json itself needs its own plugin marker — otherwise
    # the dep is classified as software-dependency and suppressed even when
    # gitignored walking is enabled.
    _mark_as_plugin(nm_dir, name="vendored", version="1.1.0")
    (nm_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "@cyanheads/git-mcp-server",
                "version": "1.1.0",
                "dependencies": {"@cyanheads/git-mcp-server": "1.1.0"},
            }
        )
    )
    (tmp_path / ".gitignore").write_text("node_modules/\n")

    runner = CliRunner()
    result_default = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
        ],
    )
    assert result_default.exit_code == 0, result_default.output

    result_opt_in = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--include-gitignored",
        ],
    )
    # Now the vendored package.json gets walked; GHSA-3q26-f695-pp76 fires.
    assert result_opt_in.exit_code == 1, result_opt_in.output
    assert "GHSA-3q26-f695-pp76" in result_opt_in.output


# ── --format mode behavior ────────────────────────────────────────────────


def test_scan_default_format_is_text(tmp_path):
    """Default output is grouped text, NOT GitHub workflow annotations.

    (GITHUB_ACTIONS auto-promotion is suppressed by the autouse fixture in
    conftest.py; tests that need it set it explicitly.)
    """
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
        ],
    )
    assert result.exit_code == 1, result.output
    # No GitHub annotation lines in the default output.
    assert not any(line.startswith("::error") for line in result.output.splitlines())
    # Grouped text format: "Found N vulnerabilities" header, severity label per
    # finding, grouped block per component.
    assert "Found " in result.output
    assert "vulnerabilit" in result.output  # vulnerability/ies
    # Severity label present.
    assert any(s in result.output for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"))


def test_scan_format_json_produces_parseable_document(tmp_path):
    """`--format json` emits a JSON document with findings + stats."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 1, result.output
    # Extract the JSON document from stdout (stderr summary is also captured
    # by CliRunner but the JSON block stands on its own).
    output = result.output
    start = output.index("{")
    # Find the matching close — the document is well-formed and indented;
    # walk to the last `}` on a line by itself for robustness.
    parsed = None
    for end in range(len(output), start, -1):
        try:
            parsed = json.loads(output[start:end])
            break
        except json.JSONDecodeError:
            continue
    assert parsed is not None
    assert isinstance(parsed["findings"], list)
    assert parsed["findings"]
    assert {"finding_type", "id", "severity", "component", "matched_advisory"} <= parsed[
        "findings"
    ][0].keys()
    assert "stats" in parsed


def test_scan_format_github_emits_annotations(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--format",
            "github",
        ],
    )
    assert result.exit_code == 1, result.output
    annotations = [line for line in result.output.splitlines() if line.startswith("::")]
    assert annotations
    assert any("file=" in line for line in annotations)


def test_scan_github_actions_env_var_auto_selects_github_format(tmp_path, monkeypatch):
    """When GITHUB_ACTIONS=true and --format is not passed, output should be
    annotations — preserves CI behavior without requiring action.yml updates."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
        ],
    )
    assert result.exit_code == 1, result.output
    annotations = [line for line in result.output.splitlines() if line.startswith("::")]
    assert annotations


def test_scan_explicit_format_text_overrides_github_actions_env(tmp_path, monkeypatch):
    """`--format text` wins over GITHUB_ACTIONS=true."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--format",
            "text",
        ],
    )
    assert result.exit_code == 1, result.output
    assert not any(line.startswith("::error") for line in result.output.splitlines())
    assert "Found " in result.output


def test_scan_no_color_strips_ansi_from_text(tmp_path):
    runner = CliRunner()
    # CliRunner's output isn't a TTY so color is already off; but exercise
    # the --no-color flag path explicitly and confirm no ANSI in output.
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--no-color",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "\x1b[" not in result.output


# ── Removed DB flag / agent-composition scope ─────────────────────────────


def test_repo_software_dep_in_non_plugin_repo_is_suppressed(tmp_path):
    """A vulnerable npm dep declared in a non-plugin repo (no
    .claude-plugin/plugin.json sibling) is classified as software-dependency
    and suppressed — OpenACA V0 is agent-composition analysis. The ACA framing
    footer explains the silence.

    (GITHUB_ACTIONS auto-promotion is suppressed by the autouse fixture in
    conftest.py — the footer only renders in `text` format.)
    """
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "regular-app",
                "version": "1.0.0",
                "dependencies": {"@cyanheads/git-mcp-server": "1.1.0"},
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "GHSA-3q26-f695-pp76" not in result.output
    assert "advisories: 0" in result.output
    assert "general-purpose SCA scanner" in result.output


def test_repo_dep_co_located_with_plugin_json_surfaces_as_agent_dep(tmp_path):
    """The same vulnerable npm dep, but the repo carries a
    .claude-plugin/plugin.json sibling — its package.json deps are now
    classified as agent-dependency and fire findings as expected."""
    _mark_as_plugin(tmp_path, name="some-plugin", version="1.0.0")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "some-plugin",
                "version": "1.0.0",
                "dependencies": {"@cyanheads/git-mcp-server": "1.1.0"},
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "GHSA-3q26-f695-pp76" in result.output


def test_stamp_source_sets_source_on_unstamped_records():
    """_stamp_source stamps source=<value> only on records that lack it.

    This guards the SARIF contract in docs/sarif-conventions.md:
    source="osv.dev" must appear on every OSV-backed finding; overlay_source
    must appear only on overlay-matched records (set by apply_overlays, not
    by _stamp_source).
    """
    from tools.scan import _stamp_source

    # Record without any source yet — should be stamped.
    unstamped: dict = {"id": "GHSA-1", "database_specific": {}}
    # Record with source already set — should be left untouched.
    prestamped: dict = {"id": "GHSA-2", "database_specific": {"openaca": {"source": "other"}}}
    # Record with no database_specific block — should get one.
    bare: dict = {"id": "GHSA-3"}

    corpus = [unstamped, prestamped, bare]
    _stamp_source(corpus, "osv.dev")

    assert unstamped["database_specific"]["openaca"]["source"] == "osv.dev"
    assert prestamped["database_specific"]["openaca"]["source"] == "other"  # not overwritten
    assert bare["database_specific"]["openaca"]["source"] == "osv.dev"
    # overlay_source is set only by apply_overlays, not by _stamp_source.
    assert "overlay_source" not in unstamped["database_specific"]["openaca"]
    assert "overlay_source" not in bare["database_specific"]["openaca"]


def test_repo_rejects_removed_db_option(tmp_path):
    """Overlay-only V0 has no user-selectable advisory DB flag."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--db",
            "ghsa",
        ],
    )
    assert result.exit_code != 0
    assert "No such option" in result.output
