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
            "repo",
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
            "repo",
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
            "repo",
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
            "repo",
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
            "repo",
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
        ["repo", "--target", str(clean), "--advisories", str(REPO_ROOT / "advisories")],
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
        ["repo", "--target", str(tmp_path), "--advisories", str(REPO_ROOT / "advisories")],
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
        ["repo", "--target", str(tmp_path), "--advisories", str(REPO_ROOT / "advisories")],
    )
    assert result.exit_code == 0
    assert "scanned 2 manifest(s)" in result.output
    assert "1 failed to parse" in result.output


def test_scan_default_output_reports_no_manifests_when_target_is_empty(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["repo", "--target", str(tmp_path), "--advisories", str(REPO_ROOT / "advisories")],
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
            "repo",
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
            "repo",
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


# Plan 007: subcommand split tests. ASVE is pre-launch, so a subcommand is
# required rather than preserving a no-subcommand compatibility shim.


def test_repo_subcommand_explicit():
    """Explicit `asve-scan repo` scans repository manifests."""
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


def test_no_subcommand_fails_with_usage():
    """Invoking `asve-scan` without a subcommand should exit non-zero with
    Click's standard usage error. There is no back-compat fallback."""
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code == 0
    assert "resolved 1 active plugin(s)" in result.output
    assert "no findings" in result.output


def test_endpoint_subcommand_matches_claude_plugin_advisory(tmp_path):
    """endpoint mode + a claude-plugin-ecosystem advisory + the minimal install
    fires a finding via the matcher's existing version-range path."""
    config_dir = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
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
            "endpoint",
            "--config-dir",
            str(config_dir),
            "--advisories",
            str(advisories_dir),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "ASVE-2026-9999" in result.output


def test_endpoint_subcommand_verbose_lists_resolved_plugins():
    config_dir = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(config_dir),
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0
    assert "claude-plugin/sample-plugin@1.2.0" in result.output
    assert "deadbeef" in result.output  # gitCommitSha shortened


def test_endpoint_verbose_non_string_git_commit_sha_does_not_crash(monkeypatch):
    """gitCommitSha from installed_plugins.json is user-editable; a non-string
    value (e.g. integer) must not crash verbose endpoint output."""
    from tools.component_ref import ComponentRef

    fake_ref = ComponentRef(
        ecosystem="claude-plugin",
        name="bad-sha-plugin",
        version="1.0.0",
        component_identity="claude-plugin/bad-sha-plugin@1.0.0",
        source_manifest="installed_plugins.json",
        source_locator="$.plugins.bad-sha-plugin@test[0]",
        attributed_to=None,
        extra={"gitCommitSha": 123, "scope": "user", "installPath": None, "marketplace": "test"},
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code == 0
    assert "resolved 0 active plugin(s)" in result.output


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
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code == 0
    # Verbose output would say which install root was picked; the smoke check
    # is that resolution succeeds and the target is not misclassified.
    assert "resolved 0 active plugin(s)" in result.output


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
            "--advisories",
            str(REPO_ROOT / "advisories"),
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"config_dir={config_dir}" in result.output
    assert "mode=endpoint" in result.output


def test_endpoint_explicit_config_dir_missing_errors(tmp_path):
    """--config-dir pointing at a non-existent path must error, not silently
    produce a false-negative 'no findings' result. Click validates this via
    exists=True on the option type."""
    missing = tmp_path / "does-not-exist"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--config-dir",
            str(missing),
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_endpoint_claude_config_dir_env_missing_errors(tmp_path, monkeypatch):
    """CLAUDE_CONFIG_DIR set to a non-existent path must error, not silently
    produce a false-negative 'no findings' result."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "does-not-exist"))
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "endpoint",
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert result.exit_code != 0
    assert "does not exist or is not a directory" in result.output


def test_fs_subcommand_is_not_kept_as_alias():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fs",
            "--target",
            str(REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"),
            "--advisories",
            str(REPO_ROOT / "advisories"),
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
        ],
    )
    assert "loaded" in result.output and "advisory(ies)" in result.output


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
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "--fail-on",
            "any",  # subcommand level: overrides → exit 1
        ],
    )
    assert result.exit_code == 1, result.output


def test_endpoint_subcommand_exclude_transitive_skips_lockfile_walk(tmp_path):
    """--exclude-transitive: Tier-2 refs suppressed; Tier-1 still emitted."""
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
    skill_dir = cache_dir / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\ndescription: x\n---\nbody\n")
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "--exclude-transitive",
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    # No lockfile refs reported. The plugin self-identity is the only
    # claude-plugin ref; lodash should NOT appear.
    assert "lodash" not in result.output
    # Tier-1 skill still emitted.
    assert "demo-skill" in result.output or "1 bundled skills" in result.output


def test_endpoint_subcommand_includes_transitive_by_default(tmp_path):
    """Without --exclude-transitive, lockfile refs are emitted."""
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


def test_endpoint_subcommand_federate_osv_augments_corpus(tmp_path):
    """--federate-osv: augment_corpus is invoked and findings include
    osv.dev-sourced advisories."""
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
                "--advisories",
                str(REPO_ROOT / "advisories"),
                "--federate-osv",
                "-v",
            ],
        )
    assert result.exit_code == 1, result.output  # finding crossed default --fail-on=any
    assert "GHSA-FAKE-LODASH" in result.output


def test_endpoint_subcommand_federate_osv_verbose_lists_queried_purls_and_skips(tmp_path):
    """Verbose + --federate-osv surfaces the actual PURLs queried and a
    per-ecosystem breakdown of refs that were skipped. Gives users insight
    into what crossed the wire to osv.dev vs what was filtered locally."""
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
                "--advisories",
                str(REPO_ROOT / "advisories"),
                "--federate-osv",
                "-v",
            ],
        )
    assert result.exit_code == 0, result.output
    # The lodash dep should appear as a queried PURL
    assert "federation: querying 1 PURL(s) on osv.dev" in result.output
    assert "pkg:npm/lodash@4.17.20" in result.output
    # The plugin self-identity ref (claude-plugin) should be in the skip count
    assert "claude-plugin=1" in result.output


def test_repo_subcommand_federate_osv_verbose_lists_queried_purls(tmp_path):
    """Same verbose surface in repo mode (parity with endpoint mode)."""
    from unittest.mock import patch

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
                "--advisories",
                str(REPO_ROOT / "advisories"),
                "--federate-osv",
                "-v",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "federation: querying" in result.output
    assert "federation: osv.dev returned 0 additional finding(s)" in result.output


def test_endpoint_subcommand_federate_osv_verbose_no_queryable_refs(tmp_path):
    """When nothing has a queryable PURL (e.g., only claude-plugin refs),
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
                "--advisories",
                str(REPO_ROOT / "advisories"),
                "--federate-osv",
                "-v",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "no queryable PURLs" in result.output


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
                "--advisories",
                str(REPO_ROOT / "advisories"),
                "--federate-osv",
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "npm:" in result.output
    assert "package-lock.json" in result.output
    assert "2 packages" in result.output or "transitive, 2" in result.output


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
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert "direct only" in result.output
    assert "package.json" in result.output


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
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    # The breakdown line should show 1 bundled MCP (the Tier-1 .mcp.json one),
    # NOT 3 (1 Tier-1 + 2 Tier-2 lockfile deps).
    assert "1 bundled MCPs" in result.output
    # The Tier-2 line should still appear separately.
    assert "transitive" in result.output and "2 packages" in result.output


def test_endpoint_verbose_lists_bare_skills_individually(tmp_path):
    """The 'bare components: N skills' summary line should be followed by
    one indented line per bare skill identity, so users can see exactly
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "bare components: 3 skills" in result.output
    # Each skill identity appears on its own line, sorted alphabetically.
    alpha_idx = result.output.find("claude-skill/alpha-skill")
    middle_idx = result.output.find("claude-skill/middle-skill")
    zebra_idx = result.output.find("claude-skill/zebra-skill")
    assert alpha_idx >= 0 and middle_idx >= 0 and zebra_idx >= 0
    assert alpha_idx < middle_idx < zebra_idx  # sorted order


def test_endpoint_verbose_omits_bare_listing_when_no_bare_components(tmp_path):
    """No bare components → no summary line and no per-component list."""
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
            "--advisories",
            str(REPO_ROOT / "advisories"),
            "-v",
        ],
    )
    assert result.exit_code == 0
    assert "bare components:" not in result.output
