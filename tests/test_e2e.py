"""End-to-end tests against the real corpus.

These exercise multiple layers together — schema/lint, exporter, parsers,
and the cross-layer "detection layer × corpus layer" promise — using the
checked-in `overlays/` directory and real schema, not synthetic fixtures.

Add new tests here as features land. Plan 005 (reference action) will add
an action-invocation roundtrip; plan 006 (disclosure policy) is doc-only
and won't add to this file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator
from packaging.version import Version

from tools import lint
from tools.bom import build_agent_bom, component_refs_from_cyclonedx
from tools.component_ref import ComponentRef
from tools.export import build
from tools.fleet.collector import _prepare_fleet_bom
from tools.osv_federation import collect_osv_queries
from tools.parsers.mcp_json import parse as parse_mcp
from tools.render import render_inventory_tree

REPO_ROOT = Path(__file__).parent.parent
OVERLAYS_DIR = REPO_ROOT / "overlays"
SCHEMA_PATH = REPO_ROOT / "schema" / "openaca.schema.json"


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
    return [(p, yaml.safe_load(p.read_text())) for p in sorted(OVERLAYS_DIR.rglob("*.yaml"))]


def test_real_corpus_lints_clean():
    """Every checked-in advisory passes the full linter against the canonical schema."""
    corpus = _load_corpus()
    assert corpus, "expected at least one overlay under overlays/"

    schema = lint.load_schema()
    validator = Draft202012Validator(schema, format_checker=lint._FORMAT_CHECKER)

    failures: list[str] = []
    for path, advisory in corpus:
        errors = (
            lint.check_schema(advisory, validator)
            + lint.check_cvss(advisory)
            + lint.check_path_consistency(advisory, path)
        )
        if errors:
            failures.append(f"{path}: {'; '.join(errors)}")
    assert not failures, "\n".join(failures)


def test_real_corpus_exports_cleanly(tmp_path):
    """`openaca export` against the real corpus produces every artifact for every YAML."""
    corpus = _load_corpus()
    expected_ids = {a["id"] for _, a in corpus}

    dist = tmp_path / "dist"
    build(OVERLAYS_DIR, schema_path=SCHEMA_PATH, dist=dist)

    for path, advisory in corpus:
        json_path = dist / "overlays" / f"{advisory['id']}.json"
        html_path = dist / "overlays" / f"{advisory['id']}.html"
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
        assert f"overlays/{advisory_id}.json" in zip_names


_HREF_RE = re.compile(r'href="([^"]+)"')


def test_index_html_links_resolve(tmp_path):
    """Every relative link in dist/index.html must point to a real file in dist/."""
    dist = tmp_path / "dist"
    build(OVERLAYS_DIR, schema_path=SCHEMA_PATH, dist=dist)
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
    identified in GHSA-3q26-f695-pp76's affected[*].
    """
    target_id = "GHSA-3q26-f695-pp76"
    advisory_path = OVERLAYS_DIR / f"{target_id}.yaml"
    if not advisory_path.exists():
        # Fixture-corpus shape can drift; skip rather than fail to avoid
        # blocking V0 evolution if the canonical sample advisory moves.
        import pytest

        pytest.skip(f"{target_id} not in corpus")

    advisory = yaml.safe_load(advisory_path.read_text())
    affected = {"ecosystem": "npm", "name": "@cyanheads/git-mcp-server"}

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
    osv_fixture = Path(__file__).parent / "fixtures" / "osv" / "ghsa-3q26-f695-pp76.json"
    osv = json.loads(osv_fixture.read_text())
    fixed = next(ev["fixed"] for ev in osv["affected"][0]["ranges"][0]["events"] if "fixed" in ev)
    pinned = matching[0].version
    assert pinned, "parser must emit a pinned version"
    assert Version(pinned) < Version(fixed), (
        f"manifest pins {pinned} but advisory says fixed in {fixed} — "
        "fixture drift means this test no longer demonstrates detection"
    )


def test_openaca_export_cli_against_real_corpus(tmp_path):
    """Smoke-test the registered console script — the path users invoke."""
    from tools.export import main as export_main

    runner = CliRunner()
    result = runner.invoke(
        export_main,
        [
            "--schema",
            str(SCHEMA_PATH),
            "--dist",
            str(tmp_path / "dist"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "dist" / "index.html").is_file()


def test_openaca_scan_cli_finds_real_advisory():
    """Plan 005 cross-layer wiring: parse_repo → matcher → SARIF, end-to-end.

    Invokes the registered `openaca scan` console script (the same path the
    Action's composite step runs) against the exposed-mcp fixture using the
    real `advisories/` corpus, and verifies it surfaces GHSA-3q26-f695-pp76 with
    a high-confidence finding. This is the V0 product promise across every
    layer behind one CLI surface.
    """
    import json

    from tools.scan import main as scan_main

    runner = CliRunner()
    sarif_path = Path(REPO_ROOT) / ".pytest-openaca-scan.sarif"
    try:
        result = runner.invoke(
            scan_main,
            [
                "repo",
                "--target",
                str(REPO_ROOT / "tests" / "fixtures" / "repos" / "exposed-mcp"),
                "--sarif",
                str(sarif_path),
            ],
        )
        # exit 1 because a finding crossed the default --fail-on=any threshold
        assert result.exit_code == 1, result.output
        sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
        rule_ids = {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
        assert "GHSA-3q26-f695-pp76" in rule_ids
        levels = [r["level"] for r in sarif["runs"][0]["results"]]
        assert "error" in levels  # high-confidence pinned-version finding
    finally:
        sarif_path.unlink(missing_ok=True)


def test_openaca_scan_attributes_bundled_finding_to_plugin():
    """Risk Attribution (plan 023) end-to-end: when a plugin bundles a
    vulnerable component, the default text output flags the *plugin* with a
    distinct `[! bundles: …]` marker, keeps the direct `[! …]` on the leaf, and
    shows the introduction `path:` — so "you installed plugin X, it's exposed
    because it bundles Y" is legible across parser → matcher → composition graph
    → renderer behind one CLI surface.
    """
    from tools.scan import main as scan_main

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            str(REPO_ROOT / "tests" / "fixtures" / "repos" / "exposed-mcp"),
            "--no-color",
        ],
    )
    assert result.exit_code == 1, result.output
    out = result.output

    # Plugin header flagged as bundling something vulnerable (containment marker).
    plugin_line = next(ln for ln in out.splitlines() if "plugin/exposed" in ln)
    assert "[! bundles: GHSA-3q26-f695-pp76]" in plugin_line

    # The bundled leaf keeps its own direct marker.
    leaf_line = next(ln for ln in out.splitlines() if "@cyanheads/git-mcp-server" in ln)
    assert "[! GHSA-3q26-f695-pp76]" in leaf_line

    # The Findings section traces how the component entered the stack.
    assert "path:" in out


def test_openaca_scan_bun_lock_surfaces_bundled_finding():
    """Risk Attribution over a bun.lock (plan 024): a bun-based plugin whose
    bun.lock pins a vulnerable transitive dep gets the [! bundles: …] marker on
    the plugin header and the direct marker on the dep leaf — across the
    bun.lock parser → matcher → composition graph → renderer. Hermetic: the
    pinned package is in conftest's offline-OSV fixture map, so no live OSV.
    """
    from tools.scan import main as scan_main

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            str(REPO_ROOT / "tests" / "fixtures" / "repos" / "bun-plugin"),
            "--no-color",
        ],
    )
    assert result.exit_code == 1, result.output
    out = result.output
    plugin_line = next(ln for ln in out.splitlines() if "bun-sample" in ln)
    assert "[! bundles: GHSA-3q26-f695-pp76]" in plugin_line
    leaf_line = next(ln for ln in out.splitlines() if "@cyanheads/git-mcp-server" in ln)
    assert "[! GHSA-3q26-f695-pp76]" in leaf_line
    # The dep was read from bun.lock, not a package.json/lock.
    assert "from bun.lock" in out


def test_pyproject_toml_detection_against_real_corpus(tmp_path):
    """Python-side cross-layer wiring: a pyproject.toml that pins a known-
    vulnerable PyPI package surfaces an GHSA-m4qw-j7mx-qv6h (aws-mcp-server)
    finding through openaca scan. Exercises the pyproject parser, the
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
            "--sarif",
            str(sarif_path),
        ],
    )
    assert result.exit_code == 1, result.output
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    rule_ids = {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
    assert "GHSA-m4qw-j7mx-qv6h" in rule_ids


# Cross-layer end-to-end tests for source-less agent component identities.
# These use in-memory advisories rather than the real corpus so the scanner
# path can be exercised with small, purpose-built fixtures.


def test_repo_mode_finds_skill_component_identity_advisory(tmp_path):
    """Cross-layer wiring for source-less skill component identity matching.

    A repo declares `.claude/skills/<name>/SKILL.md` with a versioned
    metadata.version; an in-memory advisory targets the exact logical
    component identity. Verify a high-confidence finding fires through the
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
        "id": "CVE-2026-9001",
        "modified": "2026-05-10T00:00:00Z",
        "type": "vulnerability",
        "published": "2026-05-10T00:00:00Z",
        "summary": "test",
        "details": "test",
        "affected": [],
        "severity": [
            {
                "type": "CVSS_V4",
                "score": ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
            }
        ],
        "database_specific": {
            "openaca": {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "likely",
                "component_identity": "skill/vulnerable-skill@0.9.0",
            }
        },
    }
    (advisories_dir / "CVE-2026-9001.yaml").write_text(yaml.dump(advisory))

    runner = CliRunner()
    with patch("tools.scan._load_osv_with_overlays", lambda refs: ([advisory], [], 0, {})):
        result = runner.invoke(scan_main, ["repo", "--target", str(target), "-v"])
    assert result.exit_code == 1, result.output
    assert "CVE-2026-9001" in result.output


def test_endpoint_mode_attributes_bundled_mcp_finding_to_plugin(tmp_path):
    """endpoint mode E2E: an active plugin bundles a vulnerable npm MCP via
    its `.mcp.json`. The finding fires with `attributed_to` set to
    `plugin/<name>@<version>`, surfacing in the verbose output."""
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
        "id": "CVE-2026-9002",
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
        "database_specific": {
            "openaca": {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "likely",
            }
        },
    }
    (advisories_dir / "CVE-2026-9002.yaml").write_text(yaml.dump(advisory))

    sarif_path = tmp_path / "out.sarif"
    runner = CliRunner()
    with patch("tools.scan._load_osv_with_overlays", lambda refs: ([advisory], [], 0, {})):
        result = runner.invoke(
            scan_main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
                "--sarif",
                str(sarif_path),
                "-v",
            ],
        )
    assert result.exit_code == 1, result.output
    # Verbose output surfaces the attribution suffix.
    assert "via plugin/m/vuln-plugin@1.0.0" in result.output
    # SARIF carries attributed_to in properties.
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    properties = [r.get("properties") or {} for r in sarif["runs"][0]["results"]]
    attributions = [p.get("attributed_to") for p in properties if "attributed_to" in p]
    assert "plugin/m/vuln-plugin@1.0.0" in attributions


def test_endpoint_json_output_explains_plugin_bundled_component_path(tmp_path):
    """Endpoint JSON output should identify the bundled MCP as the finding
    component while preserving the plugin container in component_path."""
    from tools.scan import main as scan_main

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
    advisory = {
        "schema_version": "1.7.1",
        "id": "CVE-2026-9004",
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
        "database_specific": {"openaca": {"source": "test"}},
    }

    runner = CliRunner()
    with patch("tools.scan._load_osv_with_overlays", lambda refs: ([advisory], [], 0, {})):
        result = runner.invoke(
            scan_main,
            [
                "endpoint",
                "--config-dir",
                str(tmp_path),
                "--format",
                "json",
            ],
        )

    assert result.exit_code == 1, result.output
    doc = json.loads(result.stdout)
    finding = next(f for f in doc["findings"] if f.get("id") == "CVE-2026-9004")
    assert finding["component"]["type"] == "mcp_server"
    assert finding["component"]["name"] == "evil"
    assert finding["declared_by"]["kind"] == "plugin"
    assert finding["declared_by"]["name"] == "vuln-plugin"
    assert finding["component_path"] == [
        {"type": "plugin", "name": "vuln-plugin"},
        {"type": "mcp_server", "name": "evil"},
    ]
    assert finding["matched_advisory"]["id"] == "CVE-2026-9004"


def test_endpoint_mode_hook_identity_match_attributes_finding(tmp_path):
    """Identity-only matching for claude-hook (ADR-0007): an advisory
    targeting a specific hook slot via `database_specific.openaca.component_identity`
    fires when a bundled hook at that slot is enumerated."""
    from tools.parsers.hooks_json import _hook_identity
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
        "id": "CVE-2026-9003",
        "modified": "2026-05-10T00:00:00Z",
        "type": "vulnerability",
        "published": "2026-05-10T00:00:00Z",
        "summary": "test",
        "details": "test",
        "affected": [],
        "severity": [
            {
                "type": "CVSS_V4",
                "score": ("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"),
            }
        ],
        "database_specific": {
            "openaca": {
                "taxonomies": {"owasp_agentic_top10": ["asi05"]},
                "evidence_level": "confirmed",
                "component_identity": _hook_identity(
                    {"type": "command", "command": "curl evil.example.com"}
                ),
            }
        },
    }
    (advisories_dir / "CVE-2026-9003.yaml").write_text(yaml.dump(advisory))

    runner = CliRunner()
    with patch("tools.scan._load_osv_with_overlays", lambda refs: ([advisory], [], 0, {})):
        result = runner.invoke(scan_main, ["endpoint", "--config-dir", str(tmp_path), "-v"])
    assert result.exit_code == 1, result.output
    assert "CVE-2026-9003" in result.output
    # Attribution propagates to the finding.
    assert "via plugin/m/hook-plugin@1.0.0" in result.output


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
            "--sarif",
            str(sarif_path),
            "-v",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "GHSA-3q26-f695-pp76" in result.output
    assert "via plugin/m/vuln-plugin@1.0.0" in result.output

    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    results = sarif["runs"][0]["results"]
    matching = [r for r in results if r.get("ruleId") == "GHSA-3q26-f695-pp76"]
    assert matching
    properties = matching[0].get("properties", {})
    assert properties.get("coverage") == "transitive"
    assert properties.get("transitive") is True
    assert properties.get("attributed_to") == "plugin/m/vuln-plugin@1.0.0"
    assert properties.get("source") == "osv.dev"
    assert properties.get("overlay_source") == "openaca.dev"


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
            "--sarif",
            str(sarif_path),
        ],
    )
    assert result.exit_code == 1, result.output
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    matching = [r for r in sarif["runs"][0]["results"] if r.get("ruleId") == "GHSA-3q26-f695-pp76"]
    assert matching
    properties = matching[0].get("properties", {})
    assert properties.get("coverage") == "transitive"
    assert properties.get("attributed_to") is None or "attributed_to" not in properties


# Identity lifecycle: BOM round-trip, rendering, OSV query filtering, and Fleet upload.


def test_github_and_docker_mcp_refs_survive_identity_lifecycle():
    sha = "0123456789abcdef0123456789abcdef01234567"
    refs = [
        ComponentRef(
            ecosystem="github",
            name="oraios/serena",
            version=sha,
            source_manifest=".mcp.json",
            source_locator="mcpServers.serena",
            extra={
                "component_type": "mcp_server",
                "install_source": (
                    f"uvx --from git+https://github.com/oraios/serena.git@{sha} "
                    "serena --token secret"
                ),
            },
        ),
        ComponentRef(
            ecosystem="docker",
            name="hashicorp/terraform-mcp-server",
            version="0.4.0",
            source_manifest=".mcp.json",
            source_locator="mcpServers.terraform",
            extra={
                "component_type": "mcp_server",
                "install_source": (
                    "docker run -i --rm -e TFE_TOKEN=${TFE_TOKEN} "
                    "hashicorp/terraform-mcp-server:0.4.0"
                ),
            },
        ),
    ]

    bom = build_agent_bom(refs, target_type="endpoint").to_cyclonedx()
    round_tripped = component_refs_from_cyclonedx(bom)

    assert [ref.ecosystem for ref in round_tripped] == ["GitHub", "Docker"]
    assert round_tripped[0].purl == f"pkg:github/oraios/serena@{sha}"
    assert round_tripped[1].purl == "pkg:docker/hashicorp/terraform-mcp-server@0.4.0"
    # The GitHub commit ref survives the round-trip as a queryable OSV git_commit
    # query; the Docker ref stays inventory-only (skipped). collect_target_purls
    # would be [] for both regardless, so it can't prove federation survived.
    assert [(q.kind, q.git_repo, q.git_ref) for q in collect_osv_queries(round_tripped)] == [
        ("git_commit", "github.com/oraios/serena", sha)
    ]

    rendered = render_inventory_tree(round_tripped, [], use_unicode=True)
    assert f"oraios/serena@{sha} (stdio via uvx)" in rendered
    assert "hashicorp/terraform-mcp-server@0.4.0 (stdio via docker)" in rendered
    assert "uvx (stdio, args hidden)" not in rendered
    assert "docker (stdio, args hidden)" not in rendered

    prepared = _prepare_fleet_bom(bom)
    github_props = _props_by_name(prepared["components"][0])
    docker_props = _props_by_name(prepared["components"][1])
    assert github_props["openaca:install_source"] == (
        f"uvx git+https://github.com/oraios/serena@{sha}"
    )
    assert docker_props["openaca:install_source"] == ("docker hashicorp/terraform-mcp-server:0.4.0")
    assert "secret" not in github_props["openaca:install_source"]
    assert "TFE_TOKEN" not in docker_props["openaca:install_source"]


def _props_by_name(component):
    return {prop["name"]: prop["value"] for prop in component.get("properties", [])}
