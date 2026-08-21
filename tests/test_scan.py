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


def test_scan_parsed_but_empty_manifest_not_reported_as_parse_failure(tmp_path):
    """A manifest that parses cleanly but emits zero refs (an empty
    `package.json`) contributes no graph-projected refs, but it parsed fine.
    The machine-format stderr summary must not claim a parse failure."""
    (tmp_path / "package.json").write_text("{}")
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--format",
            "github",
        ],
    )
    assert result.exit_code == 0
    assert "none parsed successfully" not in result.output
    assert "scanned 1 manifest(s), 0 component(s)" in result.output


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

    with patch(
        "tools.scan._load_osv_with_overlays",
        lambda refs, *, progress=None: ([advisory], [], 0, {}),
    ):
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

    with patch(
        "tools.scan._load_osv_with_overlays",
        lambda refs, *, progress=None: ([], [], 0, {}),
    ):
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


def test_endpoint_include_posture_with_no_findings_reports_ran_not_skipped(tmp_path):
    # When --include-posture runs but no rule fires, the empty result must render as
    # "posture: 0" (ran clean), not "posture: skipped" (which means not requested).
    active_dir = tmp_path / "plugins" / "cache" / "official" / "active" / "1.0.0"
    active_dir.mkdir(parents=True)
    (active_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"active": {"url": "https://active.example/mcp"}}})
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

    with (
        patch(
            "tools.scan._load_osv_with_overlays",
            lambda refs, *, progress=None: ([], [], 0, {}),
        ),
        patch("tools.scan.run_posture_rules", lambda *a, **k: []),
    ):
        ran = runner.invoke(main, ["endpoint", "--config-dir", str(tmp_path), "--include-posture"])
        skipped = runner.invoke(main, ["endpoint", "--config-dir", str(tmp_path)])

    assert ran.exit_code == 0, ran.output
    assert "posture: 0" in ran.output
    assert "posture: skipped" not in ran.output

    # Without the flag, posture is genuinely not requested -> skipped.
    assert skipped.exit_code == 0, skipped.output
    assert "posture: skipped" in skipped.output


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

    with patch(
        "tools.scan._load_osv_with_overlays",
        lambda refs, *, progress=None: ([], [], 0, {}),
    ):
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
    from tools.graph import Edge, Graph, Node

    fake_ref = ComponentRef(
        name="bad-sha-plugin",
        version="1.0.0",
        component_identity="plugin/bad-sha-plugin",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.bad-sha-plugin@test[0]",
        extra={
            "component_type": "plugin",
            "gitCommitSha": 123,
            "scope": "user",
            "installPath": None,
            "marketplace": "test",
        },
    )
    # Stage 3: scan builds the graph as source of truth, so inject the scenario
    # through that seam — a Graph whose only non-root node is the plugin carrying
    # the non-string gitCommitSha.
    root = Node(key="openaca:target", kind="target", ref=None)
    plugin = Node(key="installed_plugins.json#bad", kind="plugin", ref=fake_ref)
    graph = Graph(
        nodes={root.key: root, plugin.key: plugin},
        edges=[Edge(parent=root.key, child=plugin.key)],
    )
    graph.validate()
    monkeypatch.setattr("tools.scan.build_graph", lambda *_args, **_kwargs: graph)

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
    # Pin HOME so a real ~/.cursor on the developer machine can't be
    # detected and add its components to this Claude-only fixture scan.
    monkeypatch.setenv("HOME", str(tmp_path))

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

    def fake_augment(refs, base_corpus, *, progress=None):
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
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.2.3"}]}
                ],
            }
        ],
    }

    def fake_augment(refs, base_corpus, *, progress=None):
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
    assert "owasp-asi: ASI02, ASI05  [owasp-agentic-top-10-2026]" in result.output
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

    def fake_augment(refs, base_corpus, *, progress=None):
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

    def fake_augment(refs, base_corpus, *, progress=None):
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


def test_repo_subcommand_reports_osv_progress_for_text_output(tmp_path):
    from unittest.mock import patch

    _mark_as_plugin(tmp_path, name="demo")
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0", "dependencies": {"lodash": "4.17.20"}})
    )

    def fake_augment(refs, base_corpus, *, progress=None):
        if progress is not None:
            progress("query", 1, 1)
            progress("fetch", 1, 1)
        return list(base_corpus), []

    runner = CliRunner()
    with patch("tools.scan.augment_corpus", fake_augment):
        result = runner.invoke(
            main,
            [
                "repo",
                "--target",
                str(tmp_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert "osv.dev: querying 1 target(s)..." in result.output
    assert "osv.dev: fetching 1 advisory record(s)..." in result.output
    assert "osv.dev: fetched 1/1 advisory record(s)" in result.output


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

    def fake_augment(refs, base_corpus, *, progress=None):
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

    def fake_augment(refs, base_corpus, *, progress=None):
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

    def fake_augment(refs, base_corpus, *, progress=None):
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


def test_scan_format_json_carries_overlay_taxonomies():
    """The JSON envelope surfaces overlay taxonomies for an overlaid finding."""
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
    output = result.output
    start = output.index("{")
    parsed = None
    for end in range(len(output), start, -1):
        try:
            parsed = json.loads(output[start:end])
            break
        except json.JSONDecodeError:
            continue
    assert parsed is not None

    overlaid = [f for f in parsed["findings"] if f.get("id") == "GHSA-3q26-f695-pp76"]
    assert overlaid, parsed["findings"]
    assert overlaid[0]["taxonomies"]["owasp_agentic_top10"] == ["asi02", "asi05"]


def test_repo_scanner_skillspector_adds_external_findings(tmp_path, monkeypatch):
    skill_dir = tmp_path / ".claude" / "skills" / "deploy-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deploy-helper\n"
        "description: Helps deploy services\n"
        "---\n"
        "Run the deploy checklist.\n",
        encoding="utf-8",
    )

    def fake_collect(refs, *, progress=None):
        from tools.observations.finding import ObservationFinding
        from tools.observations.skillspector import SkillSpectorFindings
        from tools.posture.finding import PostureFinding, Standards

        skill_refs = [ref for ref in refs if (ref.extra or {}).get("component_type") == "skill"]
        assert len(skill_refs) == 1
        return SkillSpectorFindings(
            observations=[
                ObservationFinding(
                    source="skillspector",
                    source_version="0.4.0",
                    observation_id="P1",
                    title="Instruction override",
                    severity="high",
                    confidence="medium",
                    component={
                        "identity": "skill/deploy-helper",
                        "name": "deploy-helper",
                        "type": "skill",
                    },
                    subject_coordinate="sha256:test",
                    categories=["prompt-injection"],
                )
            ],
            posture_findings=[
                PostureFinding(
                    source="skillspector",
                    source_version="0.4.0",
                    rule_id="LP2",
                    title="Wildcard permission",
                    severity="medium",
                    confidence="medium",
                    component={
                        "identity": "skill/deploy-helper",
                        "name": "deploy-helper",
                        "type": "skill",
                    },
                    active_in=[],
                    declared_by={"kind": "sarif", "path": "SKILL.md"},
                    component_path=[{"type": "skill", "name": "skill/deploy-helper"}],
                    standards=Standards(),
                    remediation="Review the declared permission.",
                    evidence={"sarif_rule_id": "LP2"},
                )
            ],
            warnings=[],
        )

    monkeypatch.setattr("tools.scan.collect_skillspector_findings", fake_collect)

    result = CliRunner().invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--scanner",
            "nvidia-skillspector",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output[result.output.index("{") : result.output.rindex("}") + 1])
    observations = [
        finding for finding in parsed["findings"] if finding["finding_type"] == "observation"
    ]
    assert len(observations) == 1
    assert observations[0]["source"] == "skillspector"
    assert observations[0]["observation_id"] == "P1"
    posture = [finding for finding in parsed["findings"] if finding["finding_type"] == "posture"]
    assert posture == []

    with_posture = CliRunner().invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--scanner",
            "nvidia-skillspector",
            "--include-posture",
            "--format",
            "json",
        ],
    )

    assert with_posture.exit_code == 0, with_posture.output
    parsed = json.loads(
        with_posture.output[with_posture.output.index("{") : with_posture.output.rindex("}") + 1]
    )
    posture = [finding for finding in parsed["findings"] if finding["finding_type"] == "posture"]
    assert len(posture) == 1
    assert posture[0]["source"] == "skillspector"
    assert posture[0]["rule_id"] == "LP2"


def test_repo_scanner_skillspector_reports_progress_for_text_output(tmp_path, monkeypatch):
    skill_dir = tmp_path / ".claude" / "skills" / "deploy-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deploy-helper\n"
        "description: Helps deploy services\n"
        "---\n"
        "Run the deploy checklist.\n",
        encoding="utf-8",
    )

    def fake_collect(_refs, *, progress=None):
        from tools.observations.skillspector import SkillSpectorFindings

        assert progress is not None
        progress(1, 1)
        return SkillSpectorFindings()

    monkeypatch.setattr("tools.scan.collect_skillspector_findings", fake_collect)

    result = CliRunner().invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--scanner",
            "nvidia-skillspector",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "skillspector: scanning 1 skill(s)..." in result.output
    assert "skillspector: scanning skill 1/1" in result.output


def test_repo_scanner_skillspector_missing_command_aborts(tmp_path, monkeypatch):
    skill_dir = tmp_path / ".claude" / "skills" / "deploy-helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: deploy-helper\n"
        "description: Helps deploy services\n"
        "---\n"
        "Run the deploy checklist.\n",
        encoding="utf-8",
    )

    def missing_collect(_refs, *, progress=None):
        from tools.observations.skillspector import SkillSpectorCommandNotFound

        raise SkillSpectorCommandNotFound("SkillSpector command not found: skillspector")

    monkeypatch.setattr("tools.scan.collect_skillspector_findings", missing_collect)

    result = CliRunner().invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--scanner",
            "nvidia-skillspector",
        ],
    )

    assert result.exit_code == 1
    assert "Error: SkillSpector command not found: skillspector" in result.output


def test_repo_scanner_skillspector_missing_command_aborts_no_skills(tmp_path, monkeypatch):
    """--scanner nvidia-skillspector aborts even when the target has no skill refs.

    Previously the binary was only probed inside the per-skill loop, so a repo
    without skills silently succeeded despite an explicit --scanner request.
    """
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _cmd: None)

    result = CliRunner().invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--scanner",
            "nvidia-skillspector",
        ],
    )

    assert result.exit_code == 1
    assert "Error: SkillSpector command not found: skillspector" in result.output


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


def test_repo_skill_bundled_dep_surfaces_as_agent_dep(tmp_path):
    """A vulnerable npm dep bundled inside a `.claude/skills/<name>/` skill is
    now classified `agent-dependency` (it has a skill ancestor in the graph)
    and fires a finding. Before the composition graph this dep was filtered as
    software-dependency (ADR-0036 gap); the graph closes it."""
    skill_dir = tmp_path / ".claude" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: deploy skill\n---\nbody\n",
        encoding="utf-8",
    )
    (skill_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "deploy",
                "version": "1.0.0",
                "dependencies": {"@cyanheads/git-mcp-server": "1.1.0"},
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "--target", str(tmp_path)])
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


def test_agent_child_mcp_edge_preserved_in_graph_backed_bom():
    """The agent→MCP relationship for a frontmatter MCP child of a direct agent
    must survive into the Agent BOM.

    Stage 4 encodes the BOM from the graph: the agent→MCP edge comes directly
    from graph structure (a `dependencies[]` entry). Parentage lives purely on
    the graph edge now that `attributed_to` is removed, so the edge is preserved
    because the BOM is graph-backed."""
    from tools.bom import build_agent_bom
    from tools.component_ref import ComponentRef
    from tools.graph import Edge, Graph, Node

    agent_ref = ComponentRef(
        name="my-agent",
        version=None,
        component_identity="claude-agent/my-agent",
        source_manifest=".claude/agents/my-agent.md",
        source_locator="$.agent",
        extra={"component_type": "agent"},
    )
    mcp_ref = ComponentRef(
        name="my-mcp",
        version=None,
        component_identity="mcp/my-mcp",
        source_manifest=".claude/agents/my-agent.md",
        source_locator="$.mcpServers.my-mcp",
        extra={"component_type": "mcp_server"},
    )
    root = Node(key="openaca:target", kind="target", ref=None)
    agent_node = Node(key="agent#0", kind="agent", ref=agent_ref)
    mcp_node = Node(key="mcp#0", kind="mcp_server", ref=mcp_ref)
    graph = Graph(
        nodes={root.key: root, agent_node.key: agent_node, mcp_node.key: mcp_node},
        edges=[
            Edge(parent=root.key, child=agent_node.key),
            Edge(parent=agent_node.key, child=mcp_node.key),
        ],
    )
    graph.validate()

    # The agent→MCP edge survives via the graph-backed BOM's dependencies[].
    doc = build_agent_bom([], target_type="endpoint", target="x", graph=graph).to_cyclonedx()
    deps = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
    assert deps[agent_node.key] == [mcp_node.key]


def test_scan_endpoint_json_contains_triage_contract_fields(tmp_path):
    cache_dir = tmp_path / "cache" / "vuln-plugin" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "vuln-plugin", "version": "1.0.0"},
                    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"vuln-plugin@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "vuln-plugin@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )

    result = CliRunner().invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "--format",
            "json",
            "--fail-on",
            "none",
        ],
    )

    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    finding = output["findings"][0]
    assert finding["finding_type"] == "vulnerability"
    assert finding["component"]["type"] == "package"
    assert finding["component"]["name"] == "@cyanheads/git-mcp-server"
    assert finding["matched_advisory"]["id"] == "GHSA-3q26-f695-pp76"
    assert finding["severity"] == "UNKNOWN"
    assert finding["fixed_in"] == "1.2.3"
    assert finding["component_path"][0] == {"type": "plugin", "name": "vuln-plugin"}
    assert finding["declared_by"]["path"].endswith("package-lock.json")
    assert output["target"]["host_surface"] == "Claude Code"


def test_scan_endpoint_report_shortcut_writes_markdown_and_keeps_scan_exit(tmp_path):
    cache_dir = tmp_path / "cache" / "vuln-plugin" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "vuln-plugin", "version": "1.0.0"},
                    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"},
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"vuln-plugin@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "vuln-plugin@m": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(cache_dir)}
                    ]
                },
            }
        )
    )
    report_path = tmp_path / "report.md"

    result = CliRunner().invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "--report",
            "exposure",
            "--format",
            "markdown",
            "--output",
            str(report_path),
        ],
    )

    assert result.exit_code == 1, result.output
    report = report_path.read_text(encoding="utf-8")
    assert "# OpenACA Exposure Report" in report
    assert "vuln-plugin" in report
    assert "`upgrade`" in report


def test_scan_endpoint_report_exposure_surfaces_host_aware_posture_finding(tmp_path):
    # Verification note (this task's brief, not new production code): --report
    # exposure needs no separate host-aware wiring. tools/scan.py's `endpoint`
    # command builds `posture_output` from this task's host-aware
    # `collect_endpoint_posture_inputs` call once, then EITHER path
    # (`report_kind == "exposure"` -> `_scan_json_document(...,
    # posture_findings=posture_output, ...)`, or the plain `_emit(...,
    # posture_findings=posture_output, ...)`) reads that same list — so a
    # Cursor-attributed posture finding surfaces in the exposure report
    # exactly like a Claude one would, with no separate report-path change.
    cursor_root = tmp_path / "cursor"  # deliberately not ".cursor"
    cursor_root.mkdir()
    (cursor_root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )

    result = CliRunner().invoke(
        main,
        [
            "endpoint",
            "--host",
            "cursor",
            "--config-dir",
            str(cursor_root),
            "--include-posture",
            "--report",
            "exposure",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    evidence_ids = {
        evidence["id"]
        for card in doc["cards"]
        for evidence in card["evidence"]
        if evidence["finding_type"] == "posture"
    }
    assert "openaca-posture-insecure-transport" in evidence_ids


def test_scan_report_rejects_markdown_without_report(tmp_path):
    result = CliRunner().invoke(
        main,
        ["endpoint", "--config-dir", str(tmp_path), "--format", "markdown"],
    )

    assert result.exit_code != 0
    assert "--format markdown is only supported with --report exposure" in result.output


def test_scan_report_ignores_github_actions_auto_promotion(tmp_path, monkeypatch):
    """`--report exposure` without `--format` must still default to the text
    report even when GITHUB_ACTIONS=true — the `github` auto-promotion in
    `_apply_group_opts` would otherwise turn the advertised default report
    into a hard `--format github` rejection in CI."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = CliRunner().invoke(
        main,
        ["endpoint", "--config-dir", str(tmp_path), "--report", "exposure"],
    )

    assert result.exit_code == 0, result.output
    assert "Exposure report" in result.output
    assert "does not support --format github" not in result.output


def test_scan_repo_cursor_mcp_via_host_flag(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "--target", str(tmp_path), "--host", "cursor"])
    assert "weather-mcp" in result.output


def test_scan_repo_default_host_includes_cursor(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "--target", str(tmp_path)])  # --host omitted
    assert "weather-mcp" in result.output


def test_scan_repo_host_claude_code_only_excludes_cursor(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "--target", str(tmp_path), "--host", "claude-code"])
    assert "weather-mcp" not in result.output


def test_scan_repo_unknown_host_errors(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "--target", str(tmp_path), "--host", "not-a-real-host"])
    assert result.exit_code != 0


def test_scan_repo_duplicate_host_dedupes(tmp_path):
    # Repeated forms resolve the same duplicate away rather than erroring
    # or double-scanning: verify the manifest/component counts a duplicate
    # --host selection reports match a single-host scan exactly. A broken
    # dedup would double-count the one manifest here instead.
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    single = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "claude-code",
            "--format",
            "json",
            "--fail-on",
            "none",
        ],
    )
    duplicate = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "claude-code",
            "--host",
            "claude-code",
            "--format",
            "json",
            "--fail-on",
            "none",
        ],
    )
    assert single.exit_code == 0, single.output
    assert duplicate.exit_code == 0, duplicate.output
    single_stats = _scan_json_doc(single.output)["stats"]
    duplicate_stats = _scan_json_doc(duplicate.output)["stats"]
    assert duplicate_stats["units"] == single_stats["units"] == 1
    assert duplicate_stats["components"] == single_stats["components"] == 1


def test_scan_repo_host_comma_and_whitespace_forms_equivalent(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    runner = CliRunner()
    comma = runner.invoke(
        main, ["repo", "--target", str(tmp_path), "--host", "claude-code, cursor"]
    )
    repeated = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "claude-code",
            "--host",
            "cursor",
        ],
    )
    assert ("weather-mcp" in comma.output) == ("weather-mcp" in repeated.output) is True


def test_scan_repo_host_empty_value_errors(tmp_path):
    # A comma-only or empty --host value resolves to zero valid host
    # names after stripping — that must be a hard error ("you gave me
    # nothing usable"), not a silent scan of zero hosts (which would
    # look identical to "target has no manifests" from the outside).
    runner = CliRunner()
    result = runner.invoke(main, ["repo", "--target", str(tmp_path), "--host", ","])
    assert result.exit_code != 0


def _scan_json_doc(output: str) -> dict:
    """Pull the JSON document out of mixed stdout+stderr CliRunner output.

    Same extraction the existing `--format json` tests do: CliRunner
    captures the stderr scan summary alongside the JSON block.
    """
    start = output.index("{")
    for end in range(len(output), start, -1):
        try:
            return json.loads(output[start:end])
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"no JSON document in output: {output!r}")


def test_scan_repo_host_claude_code_excludes_cursor_posture_finding(tmp_path):
    # Confirms exclusion happens at collection, not just at labeling
    # (Steps 8-9 below fix labeling; this fixes collection).
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "claude-code",
            "--include-posture",
            "--format",
            "json",
        ],
    )
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture == []


def test_scan_repo_cursor_cache_mcp_json_posture_uses_claude_code(tmp_path):
    # Same boundary case as the registry/graph dispatch tests: a manifest
    # merely nested under .cursor/ but not the exact .cursor/mcp.json
    # shape must be posture-scanned as claude-code, not silently
    # dropped by a --host cursor selection (or, before the owning_host
    # precision fix, wrongly kept as "cursor" despite the graph never
    # recognizing it as a Cursor component at all).
    nested = tmp_path / ".cursor" / "cache"
    nested.mkdir(parents=True)
    (nested / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    cursor_only = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "cursor",
            "--include-posture",
            "--format",
            "json",
        ],
    )
    doc = _scan_json_doc(cursor_only.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture == []  # claude-code-owned manifest, not visible under --host cursor


def test_scan_repo_cursor_native_plugin_inline_mcp_servers_posture_finding(tmp_path):
    # Regression guard: `mcpServers` declared inline in `.cursor-plugin/
    # plugin.json` (not a separate mcp.json file) is inventoried by the
    # graph — `walk_plugin_root` parses `mcpServers` off the plugin.json
    # content itself, for both native plugin formats. But posture's own
    # `collect_mcp_manifests` plugin-manifest walk only matched a
    # `.claude-plugin` parent dir, so it never read a `.cursor-plugin/
    # plugin.json` at all and the insecure-transport rule missed the inline
    # servers entirely under a Cursor bundle.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "cursor",
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert len(posture) == 1
    assert posture[0]["active_in"] == ["cursor"]


def test_scan_repo_cursor_native_plugin_bundled_mcp_posture_uses_cursor(tmp_path):
    # Regression guard: a Cursor native plugin's bundled MCP manifest lives at
    # <plugin-root>/mcp.json, not the literal `.cursor/mcp.json` shape
    # `owning_host` recognizes — so it must not fall back to "claude-code".
    # Collection provenance (the graph's own runtime_hosts on the parsed
    # mcp_server ref) must override the path-shape heuristic, parity with
    # endpoint mode's `manifest_hosts`.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    (plugin_root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()

    cursor_only = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "cursor",
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert cursor_only.exit_code == 0, cursor_only.output
    doc = _scan_json_doc(cursor_only.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert len(posture) == 1  # not dropped by --host cursor
    assert posture[0]["active_in"] == ["cursor"]  # not mislabeled claude-code

    multi_host = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "cursor",
            "--host",
            "claude-code",
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert multi_host.exit_code == 0, multi_host.output
    doc = _scan_json_doc(multi_host.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert len(posture) == 1
    assert posture[0]["active_in"] == ["cursor"]  # not "claude-code"


def test_scan_repo_cursor_native_plugin_bundled_mcp_posture_excluded_when_host_unselected(
    tmp_path,
):
    # Regression guard: when Cursor isn't selected, the graph never realizes
    # this bundle (no `manifest_hosts` provenance entry), so `resolved_owner`
    # falls back to `owning_host`'s path-shape heuristic. `<plugin-root>/
    # mcp.json` doesn't match the literal `.cursor/mcp.json` shape it
    # recognizes, so it falls back to "claude-code" — which IS in the
    # selected set, so the bundle must be dropped by its own boundary
    # (`excluded_plugin_roots`), not left to leak through as a
    # Claude Code finding for a host the user explicitly excluded.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    (plugin_root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()

    claude_only = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "claude-code",
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert claude_only.exit_code == 0, claude_only.output
    doc = _scan_json_doc(claude_only.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture == []  # cursor-owned bundle, not visible under --host claude-code


def test_scan_repo_dual_native_plugin_manifests_posture_follows_realized_manifest(tmp_path):
    # Regression guard: when a bundle root carries BOTH valid native
    # manifests, graph realization picks exactly one (Claude-format wins —
    # see `_find_plugin_roots`). `collect_mcp_manifests` still globs both
    # `plugin.json` files, so the losing `.cursor-plugin/plugin.json` (with
    # its own insecure inline `mcpServers`) has no graph provenance and used
    # to fall back to `owning_host` -> "claude-code", producing a spurious
    # posture finding for content that was never actually realized as part
    # of the plugin. Only the winning manifest's content should surface.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    (plugin_root / ".cursor-plugin").mkdir()
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--host",
            "claude-code",
            "--host",
            "cursor",
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    # The losing Cursor-format manifest's insecure inline server must not
    # surface at all — it was never realized as part of the plugin.
    assert posture == []


def test_scan_repo_realized_plugin_bundle_nested_unrelated_mcp_excluded_from_posture(tmp_path):
    # Regression guard (Codex, tools/scan.py:976 on commit 9b9e7ee): in the
    # default all-host repo scan, a realized bundle's own subtree can
    # contain an unrelated nested `mcp.json` several levels deep (e.g. an
    # examples/fixtures dir) that `claude_plugin_root.walk_plugin_root`
    # never reads — it only reads the bundle-root default/custom MCP path.
    # The graph itself correctly produces no ref for this file (confirmed:
    # it's excluded from the standalone walk by `standalone_exclude_roots`),
    # but the recursive `collect_mcp_manifests([target])` posture walk still
    # matched it by bare filename with no graph provenance, so
    # `resolved_owner` fell back to `owning_host` -> "claude-code" — which
    # IS in the default all-host selection, so it leaked through unfiltered.
    # (`--host cursor` alone wouldn't reproduce this: the old fallback's
    # "claude-code" guess would already be excluded by the `hosts` filter
    # for an unrelated reason, masking the bug.)
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    nested = plugin_root / "examples" / "demo"
    nested.mkdir(parents=True)
    (nested / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    # Never read by Cursor's realization of this bundle — must not surface.
    assert posture == []


def test_scan_repo_nested_but_independently_realized_plugin_still_surfaces_posture(tmp_path):
    # Companion to the regression guard above: a nested `.cursor-plugin/
    # plugin.json` (unlike a bare `mcp.json`) is discovered by
    # `_find_plugin_roots` at ANY depth and realizes as its OWN, genuinely
    # separate plugin node (parented at target, not nested under the outer
    # plugin — single-parent invariant) whenever its own `name` is valid.
    # The fix must not treat "nested under another bundle's directory" as
    # reason enough to drop it — only content the graph never actually
    # realized should be excluded.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    nested = plugin_root / "examples" / "demo" / ".cursor-plugin"
    nested.mkdir(parents=True)
    (nested / "plugin.json").write_text(
        json.dumps({"name": "fixture", "mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    # `fixture` genuinely realized as its own plugin — its finding must survive.
    assert len(posture) == 1


def test_scan_repo_never_realized_native_plugin_manifest_excluded_from_posture(tmp_path):
    # Regression guard (Codex, tools/scan.py:1042 on commit 457e3f7): a valid
    # JSON `.claude-plugin/plugin.json` with no (or an empty) `name` never
    # produces a plugin self ref (`claude_plugin.parse`'s `if name:` gate),
    # so `_descend_into_plugin` returns `None` and the graph adds nothing —
    # no plugin node, no child refs, and (with no fallback manifest and no
    # unselected-host sibling candidate) this bundle root never lands in
    # EITHER `realized_roots` or `unselected_host_plugin_roots`. It is
    # therefore absent from both `excluded_plugin_roots` and
    # `_repo_realized_plugin_bundle_roots`'s output, so the existing
    # `_is_orphaned_under_realized_bundle` check (which only fires under a
    # root that DID realize via some other candidate) never catches it.
    # `collect_mcp_manifests`'s recursive walk still matches the file by
    # bare name, so its insecure inline `mcpServers` used to leak through as
    # a posture finding for a manifest no host ever actually loaded.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    # Never realized as a plugin by any host — must not surface.
    assert posture == []


def test_scan_repo_sibling_mcp_json_beside_never_realized_plugin_still_surfaces(tmp_path):
    # Companion to the guard above: when a bundle root never realizes at all,
    # the directory is NOT treated as an owned plugin subtree (the graph
    # comment at `graph_build.py`'s `descend` explicitly requires this — "A
    # malformed/empty `plugin.json` yields no node, so its dir must NOT be
    # excluded from sibling discovery"). A genuinely standalone `mcp.json`
    # sitting right beside the never-realized `plugin.json` is real content
    # some host could still load as an ordinary standalone manifest, so the
    # new check must not blanket-exclude the whole directory — only the
    # native `plugin.json` candidate itself.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(json.dumps({}))
    (plugin_root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert len(posture) == 1


def test_scan_repo_realized_plugin_bundle_nested_settings_excluded_from_posture(tmp_path):
    # Regression guard (Codex, tools/scan.py:1094 on commit 07178b5): the
    # settings walk (`collect_settings_manifests`) is an independent
    # recursive `rglob` over the whole target, matched only by the bare
    # `.claude/settings.json` shape — it had no awareness of the
    # realized-bundle boundary the MCP manifest list above already applies.
    # No host's plugin realization ever reads a settings.json from inside a
    # bundle (only the project's own root `.claude/settings.json` is ever
    # loaded), so a fixture like `<bundle_root>/examples/demo/.claude/
    # settings.json` with an Anthropic endpoint override used to leak
    # through as a posture finding for configuration no host ever loaded.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    nested = plugin_root / "examples" / "demo" / ".claude"
    nested.mkdir(parents=True)
    (nested / "settings.json").write_text(
        json.dumps({"anthropic_base_url": "https://proxy.example.com/api"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-api-endpoint-override"
    ]
    # Never read by any host's realization of this bundle — must not surface.
    assert posture == []


def test_scan_repo_settings_outside_bundle_still_surfaces_posture(tmp_path):
    # Companion to the guard above: a genuinely standalone
    # `.claude/settings.json` sitting outside any plugin bundle root is real
    # content Claude Code actually loads, so the realized-bundle filter must
    # not over-exclude it.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(
        json.dumps({"anthropic_base_url": "https://proxy.example.com/api"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(tmp_path),
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-api-endpoint-override"
    ]
    assert len(posture) == 1


def _with_detect(monkeypatch, host_id: str, value: bool) -> None:
    """HostAdapter is frozen — replace the registry entry, never setattr."""
    import dataclasses

    from tools.hosts import HOSTS

    monkeypatch.setitem(HOSTS, host_id, dataclasses.replace(HOSTS[host_id], detect=lambda: value))


def test_scan_endpoint_default_skips_undetected_cursor(tmp_path, monkeypatch):
    _with_detect(monkeypatch, "cursor", False)
    config_dir = tmp_path / "claude"
    (config_dir / "agents").mkdir(parents=True)
    (config_dir / "settings.json").write_text("{}")
    (config_dir / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nr\n")
    # A cursor config dir exists next door but is neither detected nor selected.
    (tmp_path / "cursor" / "agents").mkdir(parents=True)
    (tmp_path / "cursor" / "agents" / "other.md").write_text("---\nname: other\n---\no\n")

    runner = CliRunner()
    result = runner.invoke(main, ["endpoint", "--config-dir", str(config_dir)])
    assert result.exit_code == 0, result.output
    assert "host surface: Claude Code" in result.output
    assert f"config: {config_dir}" in result.output
    assert "reviewer" in result.output
    assert "other" not in result.output


def test_scan_endpoint_explicit_host_cursor_no_root_no_override_hard_error(monkeypatch):
    _with_detect(monkeypatch, "cursor", False)
    runner = CliRunner()
    result = runner.invoke(main, ["endpoint", "--host", "cursor"])
    assert result.exit_code != 0
    assert "cursor" in result.output


def test_scan_endpoint_explicit_config_dir_with_host_cursor_accepted(tmp_path, monkeypatch):
    _with_detect(monkeypatch, "cursor", False)
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()
    (cursor_root / "mcp.json").write_text('{"mcpServers": {}}')
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["endpoint", "--host", "cursor", "--config-dir", str(cursor_root)],
    )
    assert result.exit_code == 0, result.output
    assert "host surface: Cursor" in result.output


def test_scan_endpoint_unknown_host_rejected(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["endpoint", "--host", "typo", "--config-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "unknown host" in result.output


def test_scan_endpoint_config_dir_with_two_hosts_rejected(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--host",
            "claude-code",
            "--host",
            "cursor",
            "--config-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "--host" in result.output


def test_scan_endpoint_two_hosts_render_both_config_roots(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_root = tmp_path / ".claude"
    (claude_root / "agents").mkdir(parents=True)
    (claude_root / "settings.json").write_text("{}")
    (claude_root / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nr\n")
    (tmp_path / ".cursor").mkdir()

    runner = CliRunner()
    result = runner.invoke(main, ["endpoint"])
    assert result.exit_code == 0, result.output
    assert "host surface: Claude Code, Cursor" in result.output
    assert f"config (claude-code): {claude_root}" in result.output
    assert f"config (cursor): {tmp_path / '.cursor'}" in result.output
    # The shared subagent is one occurrence readable by both hosts.
    assert "reviewer" in result.output


def _with_cursor_seed(monkeypatch):
    """Stand-in for Task 17's Cursor endpoint seed (same stubbed-adapter
    pattern tests/test_graph_build.py uses): `<config_root>/mcp.json`
    servers become target children, enough to give Cursor a real graph
    component before `HOSTS["cursor"].seed_endpoint` exists for real."""
    import dataclasses

    from tools.graph import Node
    from tools.graph_build import _add_child, occurrence_key
    from tools.hosts import HOSTS
    from tools.parsers import mcp_json

    def _cursor_mcp_seed(graph, target, config_root, project_root, normalize, *, warnings=None):
        mcp_path = config_root / "mcp.json"
        if not mcp_path.is_file():
            return
        for ref in mcp_json.parse(mcp_path):
            if (ref.extra or {}).get("component_type") != "mcp_server":
                continue
            _add_child(
                graph,
                target,
                Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref),
            )

    monkeypatch.setitem(
        HOSTS, "cursor", dataclasses.replace(HOSTS["cursor"], seed_endpoint=_cursor_mcp_seed)
    )


def test_endpoint_posture_cursor_manifest_active_in_cursor(tmp_path):
    # Cursor root deliberately named "cursor", not ".cursor" — collection
    # provenance, not path shape, must drive attribution (owning_host would
    # misattribute this root to claude-code).
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()
    (cursor_root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--host",
            "cursor",
            "--config-dir",
            str(cursor_root),
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert len(posture) == 1
    assert posture[0]["active_in"] == ["cursor"]


def test_endpoint_posture_cursor_cached_plugin_bundled_mcp(tmp_path):
    # ADR-0045 Decision #7 point 5: a marketplace-cached plugin's bundled mcp.json joins
    # Cursor's endpoint posture collection, mirroring how Claude's collector
    # derives plugin install roots from its refs.
    cursor_root = tmp_path / "cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "alpha" / "deadbeef"
    (cached / ".cursor-plugin").mkdir(parents=True)
    (cached / ".cursor-plugin" / "plugin.json").write_text('{"name": "alpha"}')
    (cached / ".cache-complete").write_text("")
    (cached / "mcp.json").write_text(
        json.dumps({"mcpServers": {"api": {"url": "http://example.com/mcp"}}})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--host",
            "cursor",
            "--config-dir",
            str(cursor_root),
            "--include-posture",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert len(posture) == 1
    assert posture[0]["active_in"] == ["cursor"]


def test_scan_endpoint_cursor_only_reports_plugin_not_active_plugin(tmp_path):
    """Cursor is presence-only (ADR-0045 Decision #7): a Cursor-only endpoint scan must
    not claim "active plugin" in its text summary line, mirroring the same
    rule `bom endpoint`'s `openaca:source_unit_label` already applies."""
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()

    result = CliRunner().invoke(
        main,
        ["endpoint", "--host", "cursor", "--config-dir", str(cursor_root), "--format", "text"],
    )

    assert result.exit_code == 0, result.output
    assert "Scanned 0 plugins" in result.output
    assert "active plugin" not in result.output


def test_scan_endpoint_cursor_only_json_stats_unit_is_plugin(tmp_path):
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()

    result = CliRunner().invoke(
        main,
        ["endpoint", "--host", "cursor", "--config-dir", str(cursor_root), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    assert doc["stats"]["unit"] == "plugin"


def test_scan_endpoint_two_hosts_json_stats_unit_is_plugin(tmp_path, monkeypatch):
    """A selection that includes Cursor alongside Claude Code must still fall
    back to "plugin" — presence-only-ness of any selected host is enough,
    it doesn't require an all-Cursor selection."""
    _with_cursor_seed(monkeypatch)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_root = tmp_path / ".claude"
    claude_root.mkdir()
    (claude_root / "settings.json").write_text("{}")
    (tmp_path / ".cursor").mkdir()

    result = CliRunner().invoke(main, ["endpoint", "--format", "json"])

    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    assert doc["stats"]["unit"] == "plugin"


def test_endpoint_two_host_posture_findings_not_duplicated(tmp_path, monkeypatch):
    # Both hosts' roots via default detection; ref-keyed rules (here
    # mutable-install-reference, from each host's unpinned npx MCP) must
    # fire at most once per ref — run_posture_rules running per-host would
    # double-count them since it consumes the whole refs list each call.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    _with_cursor_seed(monkeypatch)

    claude_root = tmp_path / ".claude"
    claude_root.mkdir()
    (claude_root / "settings.json").write_text("{}")
    (claude_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"claude-tool": {"command": "npx", "args": ["claude-pkg"]}}})
    )
    cursor_root = tmp_path / ".cursor"
    cursor_root.mkdir()
    (cursor_root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"cursor-tool": {"command": "npx", "args": ["cursor-pkg"]}}})
    )

    runner = CliRunner()
    result = runner.invoke(main, ["endpoint", "--include-posture", "--format", "json"])
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    mutable = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-mutable-install-reference"
    ]
    assert len(mutable) == 2
    pairs = [(f["rule_id"], f.get("bom_ref")) for f in mutable]
    assert len(pairs) == len(set(pairs))


def test_endpoint_posture_dispatch_runs_once_over_union(tmp_path, monkeypatch):
    # Spy on run_posture_rules: exactly ONE call; its manifests argument is
    # the deduped (path, dict) union of both hosts' collector outputs, and
    # manifest_hosts maps each path to the host that collected it.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    claude_root = tmp_path / ".claude"
    claude_root.mkdir()
    (claude_root / "settings.json").write_text("{}")
    (claude_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"claude-tool": {"url": "http://claude.example.com/mcp"}}})
    )
    cursor_root = tmp_path / ".cursor"
    cursor_root.mkdir()
    (cursor_root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"cursor-tool": {"url": "http://cursor.example.com/mcp"}}})
    )

    import tools.scan as scan_module

    calls: list[tuple[list, dict | None]] = []
    real_run_posture_rules = scan_module.run_posture_rules

    def spy(refs, manifests, settings_manifests, manifest_hosts=None):
        calls.append((manifests, manifest_hosts))
        return real_run_posture_rules(
            refs, manifests, settings_manifests, manifest_hosts=manifest_hosts
        )

    monkeypatch.setattr(scan_module, "run_posture_rules", spy)

    runner = CliRunner()
    result = runner.invoke(main, ["endpoint", "--include-posture", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    manifests, manifest_hosts = calls[0]
    paths = [path for path, _ in manifests]
    assert len(paths) == len(set(paths))
    assert set(paths) == {claude_root / ".mcp.json", cursor_root / "mcp.json"}
    assert manifest_hosts == {
        claude_root / ".mcp.json": "claude-code",
        cursor_root / "mcp.json": "cursor",
    }


def test_endpoint_posture_claude_settings_layer_unchanged(tmp_path, monkeypatch):
    # A claude settings.json mcpServers autoApprove entry still produces the
    # mcp_auto_approve finding through collect_endpoint_settings_manifests —
    # the settings path is untouched by the collect_endpoint_posture_inputs
    # refactor.
    _with_detect(monkeypatch, "cursor", False)
    config_dir = tmp_path / "claude"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps(
            {"mcpServers": {"tool": {"command": "npx", "args": ["pkg"], "autoApprove": True}}}
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["endpoint", "--config-dir", str(config_dir), "--include-posture", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    auto_approve = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-mcp-auto-approve"
    ]
    assert len(auto_approve) == 1


def test_scan_repo_settings_inside_unselected_bundle_excluded_from_posture(tmp_path):
    # The settings walk is an independent filesystem pass — it must honor the
    # unselected-host bundle boundary the MCP manifest list already does. A
    # `.claude/settings.json` inside an excluded Cursor bundle must not
    # produce api-endpoint-override findings under --host claude-code, while
    # the same file outside the bundle still does.
    bundle = tmp_path / "cursor-bundle"
    (bundle / ".cursor-plugin").mkdir(parents=True)
    (bundle / ".cursor-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}))
    (bundle / ".claude").mkdir()
    (bundle / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com/api"}})
    )
    runner = CliRunner()

    def _override_findings(target):
        result = runner.invoke(
            main,
            [
                "repo",
                "--target",
                str(target),
                "--host",
                "claude-code",
                "--include-posture",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        doc = _scan_json_doc(result.output)
        return [
            f
            for f in doc["findings"]
            if f.get("finding_type") == "posture"
            and f.get("rule_id") == "openaca-posture-api-endpoint-override"
        ]

    assert _override_findings(tmp_path) == []

    control = tmp_path / "control"
    (control / ".claude").mkdir(parents=True)
    (control / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com/api"}})
    )
    assert _override_findings(control) != []
