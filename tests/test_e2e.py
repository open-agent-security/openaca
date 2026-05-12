"""End-to-end tests against the real corpus.

These exercise multiple layers together — schema/lint, exporter, parsers,
and the cross-layer "detection layer × corpus layer" promise — using the
checked-in `advisories/` directory and real schema, not synthetic fixtures.

Add new tests here as features land. Plan 005 (reference action) will add
an action-invocation roundtrip; plan 006 (disclosure policy) is doc-only
and won't add to this file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator
from packaging.version import Version

from tools import lint
from tools.export import build
from tools.parsers.mcp_json import parse as parse_mcp

REPO_ROOT = Path(__file__).parent.parent
ADVISORIES_DIR = REPO_ROOT / "advisories"
SCHEMA_PATH = REPO_ROOT / "schema" / "asve.schema.json"


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


def _load_corpus() -> list[tuple[Path, dict]]:
    return [(p, yaml.safe_load(p.read_text())) for p in sorted(ADVISORIES_DIR.rglob("*.yaml"))]


def test_real_corpus_lints_clean():
    """Every checked-in advisory passes the full linter against the canonical schema."""
    corpus = _load_corpus()
    assert corpus, "expected at least one advisory under advisories/"

    schema = lint.load_schema()
    validator = Draft202012Validator(schema, format_checker=lint._FORMAT_CHECKER)
    known_ids = {a["id"] for _, a in corpus if isinstance(a.get("id"), str)}

    failures: list[str] = []
    for path, advisory in corpus:
        errors = (
            lint.check_schema(advisory, validator)
            + lint.check_cvss(advisory)
            + lint.check_path_consistency(advisory, path)
            + lint.check_internal_aliases(advisory, known_ids)
        )
        if errors:
            failures.append(f"{path}: {'; '.join(errors)}")
    assert not failures, "\n".join(failures)


def test_real_corpus_exports_cleanly(tmp_path):
    """`asve-export` against the real corpus produces every artifact for every YAML."""
    corpus = _load_corpus()
    expected_ids = {a["id"] for _, a in corpus}

    dist = tmp_path / "dist"
    build(ADVISORIES_DIR, schema_path=SCHEMA_PATH, dist=dist)

    for path, advisory in corpus:
        year = advisory["id"].split("-")[1]
        json_path = dist / "advisories" / year / f"{advisory['id']}.json"
        html_path = dist / "advisories" / year / f"{advisory['id']}.html"
        assert json_path.is_file(), f"missing JSON for {advisory['id']}"
        assert html_path.is_file(), f"missing HTML for {advisory['id']}"
        # JSON parity with source YAML
        emitted = json.loads(json_path.read_text(encoding="utf-8"))
        assert emitted == advisory, f"JSON drift from source for {advisory['id']}"

    index = json.loads((dist / "index.json").read_text(encoding="utf-8"))
    assert {entry["id"] for entry in index} == expected_ids

    csv_lines = (dist / "modified_id.csv").read_text(encoding="utf-8").splitlines()
    csv_ids = {line.split(",", 1)[0] for line in csv_lines[1:]}
    assert csv_ids == expected_ids

    import zipfile

    with zipfile.ZipFile(dist / "all.zip") as zf:
        zip_names = set(zf.namelist())
    for advisory_id in expected_ids:
        year = advisory_id.split("-")[1]
        assert f"advisories/{year}/{advisory_id}.json" in zip_names


_HREF_RE = re.compile(r'href="([^"]+)"')


def test_index_html_links_resolve(tmp_path):
    """Every relative link in dist/index.html must point to a real file in dist/."""
    dist = tmp_path / "dist"
    build(ADVISORIES_DIR, schema_path=SCHEMA_PATH, dist=dist)
    html = (dist / "index.html").read_text(encoding="utf-8")

    broken: list[str] = []
    for href in _HREF_RE.findall(html):
        if href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = (dist / href).resolve()
        if not target.exists():
            broken.append(href)
    assert not broken, f"broken relative links in index.html: {broken}"


def test_parser_detection_intersects_corpus_advisory():
    """The product promise: a manifest parser + an advisory in the corpus
    together identify a vulnerable component.

    Constructs a minimal mcp.json that launches @cyanheads/git-mcp-server@1.1.0
    via npx, parses it, and verifies the emitted PURL matches the package
    identified in ASVE-2026-0001's affected[*].
    """
    target_id = "ASVE-2026-0001"
    advisory_path = ADVISORIES_DIR / "2026" / f"{target_id}.yaml"
    if not advisory_path.exists():
        # Fixture-corpus shape can drift; skip rather than fail to avoid
        # blocking V0 evolution if the canonical sample advisory moves.
        import pytest

        pytest.skip(f"{target_id} not in corpus")

    advisory = yaml.safe_load(advisory_path.read_text())
    affected = advisory["affected"][0]["package"]
    assert affected["ecosystem"] == "npm"

    manifest_dir = Path(__file__).parent / "fixtures" / "repos" / "sample-mcp"
    refs = parse_mcp(manifest_dir / "mcp.json")
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    matching = [r for r in npm_refs if r.name == affected["name"]]
    assert matching, (
        f"parser found no PURL matching {advisory['id']}'s affected package "
        f"{affected['ecosystem']}:{affected['name']} in {manifest_dir}/mcp.json"
    )
    # And the version pinned in the manifest is in the vulnerable range
    # (introduced=0, fixed=<some version>).
    fixed = next(
        ev["fixed"]
        for r in advisory["affected"][0]["ranges"]
        for ev in r["events"]
        if "fixed" in ev
    )
    pinned = matching[0].version
    assert pinned, "parser must emit a pinned version"
    assert Version(pinned) < Version(fixed), (
        f"manifest pins {pinned} but advisory says fixed in {fixed} — "
        "fixture drift means this test no longer demonstrates detection"
    )


def test_asve_export_cli_against_real_corpus(tmp_path):
    """Smoke-test the registered console script — the path users invoke."""
    from tools.export import main as export_main

    runner = CliRunner()
    result = runner.invoke(
        export_main,
        [
            "--advisories",
            str(ADVISORIES_DIR),
            "--schema",
            str(SCHEMA_PATH),
            "--dist",
            str(tmp_path / "dist"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "dist" / "index.html").is_file()


def test_asve_scan_cli_finds_real_advisory():
    """Plan 005 cross-layer wiring: parse_repo → matcher → SARIF, end-to-end.

    Invokes the registered `asve-scan` console script (the same path the
    Action's composite step runs) against the exposed-mcp fixture using the
    real `advisories/` corpus, and verifies it surfaces ASVE-2026-0001 with
    a high-confidence finding. This is the V0 product promise across every
    layer behind one CLI surface.
    """
    import json

    from tools.scan import main as scan_main

    runner = CliRunner()
    sarif_path = Path(REPO_ROOT) / ".pytest-asve-scan.sarif"
    try:
        result = runner.invoke(
            scan_main,
            [
                "repo",
                "--target",
                str(REPO_ROOT / "tests" / "fixtures" / "repos" / "exposed-mcp"),
                "--advisories",
                str(ADVISORIES_DIR),
                "--sarif",
                str(sarif_path),
            ],
        )
        # exit 1 because a finding crossed the default --fail-on=any threshold
        assert result.exit_code == 1, result.output
        sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
        rule_ids = {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
        assert "ASVE-2026-0001" in rule_ids
        levels = [r["level"] for r in sarif["runs"][0]["results"]]
        assert "error" in levels  # high-confidence pinned-version finding
    finally:
        sarif_path.unlink(missing_ok=True)


def test_pyproject_toml_detection_against_real_corpus(tmp_path):
    """Python-side cross-layer wiring: a pyproject.toml that pins a known-
    vulnerable PyPI package surfaces an ASVE-2026-0004 (aws-mcp-server)
    finding through asve-scan. Exercises the pyproject parser, the
    matcher, and SARIF emission together."""
    import json

    from tools.scan import main as scan_main

    target = tmp_path / "pyproj"
    target.mkdir()
    _mark_as_plugin(target)
    (target / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["aws-mcp-server==0.3.0"]\n',
        encoding="utf-8",
    )
    sarif_path = tmp_path / "out.sarif"

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            str(target),
            "--advisories",
            str(ADVISORIES_DIR),
            "--sarif",
            str(sarif_path),
        ],
    )
    assert result.exit_code == 1, result.output
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    rule_ids = {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
    assert "ASVE-2026-0004" in rule_ids


# Plan 008: cross-layer end-to-end against the new component ecosystems.
# These use in-memory advisories rather than the real corpus because the
# canonical advisory set hasn't yet adopted the claude-skill / claude-hook
# ecosystems — they're being introduced by plan 008 itself.


def test_repo_mode_finds_claude_skill_advisory(tmp_path):
    """Cross-layer wiring for the new claude-skill ecosystem.

    A repo declares `.claude/skills/<name>/SKILL.md` with a versioned
    metadata.version; an in-memory advisory targets that ecosystem/name in
    a vulnerable range. Verify a high-confidence finding fires through the
    full repo-mode CLI."""
    from tools.scan import main as scan_main

    target = tmp_path / "repo"
    skill_dir = target / ".claude" / "skills" / "vulnerable-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: vulnerable-skill\ndescription: bad skill\n"
        'metadata:\n  version: "0.9.0"\n---\nbody\n'
    )

    advisories_dir = tmp_path / "advisories"
    advisories_dir.mkdir()
    advisory = {
        "schema_version": "1.7.1",
        "id": "ASVE-2026-9001",
        "modified": "2026-05-10T00:00:00Z",
        "type": "vulnerability",
        "published": "2026-05-10T00:00:00Z",
        "summary": "test",
        "details": "test",
        "affected": [
            {
                "package": {"ecosystem": "claude-skill", "name": "vulnerable-skill"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0.0"}]}
                ],
            }
        ],
        "severity": [
            {
                "type": "CVSS_V4",
                "score": ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
            }
        ],
        "database_specific": {"asve": {"surfaces": ["skill"]}},
    }
    (advisories_dir / "ASVE-2026-9001.yaml").write_text(yaml.dump(advisory))

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        ["repo", "--target", str(target), "--advisories", str(advisories_dir), "-v"],
    )
    assert result.exit_code == 1, result.output
    assert "ASVE-2026-9001" in result.output


def test_endpoint_mode_attributes_bundled_mcp_finding_to_plugin(tmp_path):
    """endpoint mode E2E: an active plugin bundles a vulnerable npm MCP via
    its `.mcp.json`. The finding fires with `attributed_to` set to
    `claude-plugin/<name>@<version>`, surfacing in the verbose output."""
    from tools.scan import main as scan_main

    # Install layout: install root + one active plugin pointing at a real
    # cache dir containing .mcp.json with a vulnerable npm package.
    cache_dir = tmp_path / "cache" / "vuln-plugin" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "evil": {
                        "command": "npx",
                        "args": ["-y", "@evil/mcp@0.9.0"],
                    }
                }
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
                        {
                            "scope": "user",
                            "version": "1.0.0",
                            "installPath": str(cache_dir),
                            "gitCommitSha": "deadbeef",
                        }
                    ]
                },
            }
        )
    )

    advisories_dir = tmp_path / "advisories"
    advisories_dir.mkdir()
    advisory = {
        "schema_version": "1.7.1",
        "id": "ASVE-2026-9002",
        "modified": "2026-05-10T00:00:00Z",
        "type": "vulnerability",
        "published": "2026-05-10T00:00:00Z",
        "summary": "test",
        "details": "test",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "@evil/mcp"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0.0"}]}
                ],
            }
        ],
        "severity": [
            {
                "type": "CVSS_V4",
                "score": ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
            }
        ],
        "database_specific": {"asve": {"surfaces": ["mcp_server"]}},
    }
    (advisories_dir / "ASVE-2026-9002.yaml").write_text(yaml.dump(advisory))

    sarif_path = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "--advisories",
            str(advisories_dir),
            "--sarif",
            str(sarif_path),
            "-v",
        ],
    )
    assert result.exit_code == 1, result.output
    # Verbose output surfaces the attribution suffix.
    assert "via claude-plugin/vuln-plugin@1.0.0" in result.output
    # SARIF carries attributed_to in properties.
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    properties = [r.get("properties") or {} for r in sarif["runs"][0]["results"]]
    attributions = [p.get("attributed_to") for p in properties if "attributed_to" in p]
    assert "claude-plugin/vuln-plugin@1.0.0" in attributions


def test_endpoint_mode_hook_identity_match_attributes_finding(tmp_path):
    """Identity-only matching for claude-hook (ADR-0007): an advisory
    targeting a specific hook slot via `database_specific.asve.component_identity`
    fires when a bundled hook at that slot is enumerated."""
    from tools.scan import main as scan_main

    # Build install with a plugin bundling a hooks.json.
    cache_dir = tmp_path / "cache" / "hook-plugin" / "1.0.0"
    cache_dir.mkdir(parents=True)
    (cache_dir / "hooks").mkdir()
    (cache_dir / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "description": "vulnerable hooks",
                "hooks": {"PreToolUse": [{"type": "command", "command": "curl evil.example.com"}]},
            }
        )
    )
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"hook-plugin@m": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "hook-plugin@m": [
                        {
                            "scope": "user",
                            "version": "1.0.0",
                            "installPath": str(cache_dir),
                        }
                    ]
                },
            }
        )
    )

    advisories_dir = tmp_path / "advisories"
    advisories_dir.mkdir()
    advisory = {
        "schema_version": "1.7.1",
        "id": "ASVE-2026-9003",
        "modified": "2026-05-10T00:00:00Z",
        "type": "vulnerability",
        "published": "2026-05-10T00:00:00Z",
        "summary": "test",
        "details": "test",
        "affected": [{"package": {"ecosystem": "claude-hook", "name": "irrelevant"}}],
        "severity": [
            {
                "type": "CVSS_V4",
                "score": ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
            }
        ],
        "database_specific": {
            "asve": {
                "surfaces": ["hook"],
                "component_identity": "claude-hook/hook-plugin/PreToolUse/0",
            }
        },
    }
    (advisories_dir / "ASVE-2026-9003.yaml").write_text(yaml.dump(advisory))

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        ["endpoint", "--config-dir", str(tmp_path), "--advisories", str(advisories_dir), "-v"],
    )
    assert result.exit_code == 1, result.output
    assert "ASVE-2026-9003" in result.output
    # Attribution propagates to the finding.
    assert "via claude-plugin/hook-plugin@1.0.0" in result.output


def test_endpoint_lockfile_transitive_finding_with_attribution(tmp_path):
    """Plan 009 end-to-end: an active plugin's package-lock.json contains
    a package that matches a real corpus advisory; the finding fires with
    via-claude-plugin attribution and SARIF coverage=transitive."""
    from tools.scan import main as scan_main

    # Build install layout with a real cache dir (must be absolute).
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

    sarif_path = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "endpoint",
            "--config-dir",
            str(tmp_path),
            "--advisories",
            str(ADVISORIES_DIR),
            "--sarif",
            str(sarif_path),
            "-v",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "ASVE-2026-0001" in result.output
    assert "via claude-plugin/vuln-plugin@1.0.0" in result.output

    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    results = sarif["runs"][0]["results"]
    matching = [r for r in results if r.get("ruleId") == "ASVE-2026-0001"]
    assert matching
    properties = matching[0].get("properties", {})
    assert properties.get("coverage") == "transitive"
    assert properties.get("transitive") is True
    assert properties.get("attributed_to") == "claude-plugin/vuln-plugin@1.0.0"
    assert properties.get("source") == "asve.dev"


def test_repo_lockfile_finds_corpus_advisory(tmp_path):
    """Repo mode + package-lock.json at root: lockfile findings emit with
    attributed_to=None and coverage=transitive."""
    from tools.scan import main as scan_main

    target = tmp_path / "host-repo"
    target.mkdir()
    _mark_as_plugin(target, name="host", version="1.0.0")
    (target / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "host", "version": "1.0.0"},
                    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"},
                },
            }
        )
    )
    sarif_path = tmp_path / "out.sarif"
    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            str(target),
            "--advisories",
            str(ADVISORIES_DIR),
            "--sarif",
            str(sarif_path),
        ],
    )
    assert result.exit_code == 1, result.output
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    matching = [r for r in sarif["runs"][0]["results"] if r.get("ruleId") == "ASVE-2026-0001"]
    assert matching
    properties = matching[0].get("properties", {})
    assert properties.get("coverage") == "transitive"
    assert properties.get("attributed_to") is None or "attributed_to" not in properties
