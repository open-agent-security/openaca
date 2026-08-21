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

import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator
from packaging.version import Version

from tools import lint
from tools.bom import build_agent_bom, component_refs_from_cyclonedx
from tools.component_ref import ComponentRef
from tools.export import build
from tools.osv_federation import collect_osv_queries
from tools.parsers.mcp_json import parse as parse_mcp
from tools.remote.collector import _prepare_remote_bom
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


def test_openaca_scan_json_carries_exposure_triage_contract():
    """Plan 036 product contract: scan JSON carries enough composition and
    finding evidence for exposure triage without re-reading the target."""
    from tools.scan import main as scan_main

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
            "repo",
            "--target",
            str(REPO_ROOT / "tests" / "fixtures" / "repos" / "exposed-mcp"),
            "--format",
            "json",
            "--fail-on",
            "none",
        ],
    )
    assert result.exit_code == 0, result.output
    scan_doc = json.loads(result.stdout)
    finding = scan_doc["findings"][0]
    assert finding["finding_type"] == "vulnerability"
    assert finding["matched_advisory"]["id"] == "GHSA-3q26-f695-pp76"
    assert finding["severity"] == "UNKNOWN"
    assert finding["fixed_in"] == "1.2.3"
    assert finding["component_path"] == [
        {"type": "plugin", "name": "exposed"},
        {"type": "package", "name": "@cyanheads/git-mcp-server"},
    ]
    assert finding["declared_by"]["path"].endswith("package.json")
    assert scan_doc["target"]["host_surface"] == "repository"


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


def _write_subdir_plugin_with_root_pkg(tmp_path, mcp_entry, root_name="@acme/dc"):
    """DesktopCommander shape: a plugin declared in a subdirectory whose MCP
    server is `mcp_entry`, with the implementation deps in the repo-root
    package.json (named `root_name`)."""
    target = tmp_path / "repo"
    plugin_dir = target / "plugins" / "claude" / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "dc", "version": "1.0.0", "mcpServers": {"dc": mcp_entry}}),
        encoding="utf-8",
    )
    (target / "package.json").write_text(
        json.dumps(
            {
                "name": root_name,
                "version": "1.0.0",
                "dependencies": {"@cyanheads/git-mcp-server": "1.1.0"},
            }
        ),
        encoding="utf-8",
    )
    return target


def test_mcp_npx_self_launch_surfaces_root_dep_advisory(tmp_path):
    """ADR-0039: a subdir plugin whose MCP server launches the repo's own
    published package via `npx` surfaces the root-level dependency advisory —
    the launch resolves to the root manifest, the deps are re-parented under the
    mcp_server (agent-dependency), and the finding fires. Hermetic via the
    offline-OSV fixture map (@cyanheads/git-mcp-server@1.1.0 → GHSA-3q26-f695-pp76).
    """
    from tools.scan import main as scan_main

    target = _write_subdir_plugin_with_root_pkg(
        tmp_path,
        {"command": "npx", "args": ["-y", "@acme/dc@latest"]},
        root_name="@acme/dc",
    )
    result = CliRunner().invoke(scan_main, ["repo", "--target", str(target), "--no-color"])
    assert result.exit_code == 1, result.output
    assert "GHSA-3q26-f695-pp76" in result.output


def test_mcp_remote_url_does_not_surface_root_deps(tmp_path):
    """Counterpart: the same root dep, but the MCP server is remote (`url`).
    Nothing executes locally, so the launch resolves to nothing, the root deps
    stay software-dependency, and the advisory does NOT fire — ADR-0039 attributes
    only to a resolvable launch, not to "the repo has a component"."""
    from tools.scan import main as scan_main

    target = _write_subdir_plugin_with_root_pkg(tmp_path, {"url": "https://mcp.example.com/mcp"})
    result = CliRunner().invoke(scan_main, ["repo", "--target", str(target), "--no-color"])
    assert result.exit_code == 0, result.output
    assert "GHSA-3q26-f695-pp76" not in result.output


def test_skill_bundled_dependency_detected_and_nested(tmp_path):
    """Plan 033 marquee (closes the ADR-0036 gap): a skill bundling a vulnerable
    `package.json` dep is detected, nested under the skill (graph-native: the
    package's identity is `package/{eco}/{name}` and its parentage is purely the
    graph edge), scoped `agent-dependency`, and attributed to the skill's plugin.

    This is the product promise the composition graph unlocks once `attributed_to`
    is gone: the package node is `target → plugin → skill → package`, so the dep
    surfaces (it is no longer filtered as software-dependency) and attribution is
    derived from the graph lineage rather than a stored string.
    """
    from tools.scan import main as scan_main

    target = tmp_path / "repo"
    (target / ".claude-plugin").mkdir(parents=True)
    (target / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "vuln-bundle", "version": "1.0.0"}), encoding="utf-8"
    )
    skill_dir = target / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: deploy skill\n---\nrun\n", encoding="utf-8"
    )
    # @cyanheads/git-mcp-server@1.1.0 is vulnerable (< 1.2.3, GHSA-3q26-f695-pp76)
    # and lives in conftest's offline-OSV fixture map, so the scan stays hermetic.
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
    sarif_path = tmp_path / "out.sarif"
    bom_path = tmp_path / "out.bom.json"

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        ["repo", "--target", str(target), "--no-color", "--sarif", str(sarif_path)],
    )

    # The advisory fires: a vulnerable agent component was found.
    assert result.exit_code == 1, result.output
    out = result.output

    # Inventory tree nests the package under the skill, which nests under the
    # plugin (graph edges, not a stored attribution string).
    plugin_idx = out.index("plugin/vuln-bundle@1.0.0")
    skill_idx = out.index("deploy (from skills/deploy/SKILL.md)")
    pkg_idx = out.index("@cyanheads/git-mcp-server@1.1.0")
    assert plugin_idx < skill_idx < pkg_idx
    assert "package deps/ (1)" in out
    # The dep keeps its own finding marker; the plugin gets the containment marker.
    assert "@cyanheads/git-mcp-server@1.1.0 (from skills/deploy/package.json)" in out
    assert "[! bundles: GHSA-3q26-f695-pp76]" in out

    # SARIF attributes the finding "via" the skill's plugin, derived from lineage.
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    (sarif_result,) = sarif["runs"][0]["results"]
    assert sarif_result["properties"]["attributed_to"] == "plugin/vuln-bundle@1.0.0"

    # The Agent BOM scopes the package agent-dependency (not suppressed software-
    # dependency) and uses the graph-native identity `package/{eco}/{name}`.
    from tools.bom_cli import main as bom_main

    bom_result = runner.invoke(
        bom_main, ["repo", "--target", str(target), "--output", str(bom_path)]
    )
    assert bom_result.exit_code == 0, bom_result.output
    doc = json.loads(bom_path.read_text(encoding="utf-8"))
    pkg_component = next(c for c in doc["components"] if "git-mcp-server" in (c.get("name") or ""))
    pkg_props = {p["name"]: p["value"] for p in pkg_component.get("properties", [])}
    assert pkg_props["openaca:scope"] == "agent-dependency"
    assert pkg_props["openaca:identity"] == "package/npm/@cyanheads/git-mcp-server"


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


# Cross-layer end-to-end tests for source-less agent component graph identities.
# These use in-memory advisories rather than the real corpus so the scanner
# path can be exercised with small, purpose-built fixtures.


def test_repo_mode_skill_graph_identity_is_inventory_only(tmp_path):
    """A skill graph identity by itself is inventory data, not a vuln match key.

    A repo declares `.claude/skills/<name>/SKILL.md` with a versioned
    metadata.version; an in-memory advisory targets the exact graph identity.
    The CLI should still inventory the skill, but should not emit a finding."""
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
    with patch(
        "tools.scan._load_osv_with_overlays",
        lambda refs, *, progress=None: ([advisory], [], 0, {}),
    ):
        result = runner.invoke(scan_main, ["repo", "--target", str(target), "-v"])
    assert result.exit_code == 0, result.output
    assert "vulnerable-skill@0.9.0" in result.output
    assert "No advisories matched" in result.output


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
    with patch(
        "tools.scan._load_osv_with_overlays",
        lambda refs, *, progress=None: ([advisory], [], 0, {}),
    ):
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
    with patch(
        "tools.scan._load_osv_with_overlays",
        lambda refs, *, progress=None: ([advisory], [], 0, {}),
    ):
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


def test_endpoint_mode_hook_graph_identity_is_inventory_only(tmp_path):
    """Hook graph identity alone does not match vulnerability advisories."""
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
    with patch(
        "tools.scan._load_osv_with_overlays",
        lambda refs, *, progress=None: ([advisory], [], 0, {}),
    ):
        result = runner.invoke(scan_main, ["endpoint", "--config-dir", str(tmp_path), "-v"])
    assert result.exit_code == 0, result.output
    assert "curl evil.example.com" in result.output
    assert "No advisories matched" in result.output


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
    """Repo mode + package-lock.json at a plugin root: lockfile findings emit with
    coverage=transitive and are attributed to the enclosing plugin. Per ADR-0037,
    attribution is the nearest plugin ancestor in the composition graph; the repo
    root IS the `host` plugin, so its own transitive deps attribute to it (the
    pre-graph behavior of attributed_to=None in repo mode is superseded)."""
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
    assert properties.get("attributed_to") == "plugin/host@1.0.0"
    assert properties["taxonomies"]["owasp_agentic_top10"] == ["asi02", "asi05"]


def test_default_scan_text_shows_agentic_taxonomy_from_real_corpus(tmp_path):
    """Corpus overlay -> matcher -> default text card, without -v.

    Fails if the overlay loader stops merging `database_specific.openaca`, if the
    matcher stops attaching the advisory, or if the renderer re-gates the agentic
    line behind verbose. Per ADR-0043 the default card shows only the agentic
    family.
    """
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
    runner = CliRunner()
    result = runner.invoke(scan_main, ["repo", "--target", str(target)])

    assert result.exit_code == 1, result.output
    assert "GHSA-3q26-f695-pp76" in result.output
    assert "owasp-asi: ASI02, ASI05  [owasp-agentic-top-10-2026]" in result.output


# Identity lifecycle: BOM round-trip, rendering, OSV query filtering, and remote upload.


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

    prepared = _prepare_remote_bom(bom)
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


def _scan_json_doc(output: str) -> dict:
    """Pull the JSON document out of mixed stdout+stderr CliRunner output.

    Same extraction `tests/test_scan.py`'s `_scan_json_doc` does: CliRunner
    captures the stderr scan summary alongside the JSON block.
    """
    start = output.index("{")
    for end in range(len(output), start, -1):
        try:
            return json.loads(output[start:end])
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"no JSON document in output: {output!r}")


def test_agent_bom_carries_capability_descriptors_for_both_tiers(tmp_path):
    """Plan 037 marquee: a scan of a repo with (a) a skill declaring
    `allowed-tools: Bash` and (b) an MCP server launched via
    `npx @modelcontextprotocol/server-filesystem` produces an Agent BOM where
    both components carry capability descriptors — the skill's declared
    `shell_exec` and the curated `file_read`/`file_write` for the filesystem
    server — with `partial` coverage, across the declared extractor, the real
    curated corpus, the orchestrator, the composition graph, and the BOM
    emitter behind the `bom repo` CLI.

    The MCP entry matches the checked-in seed via its npm package coordinate
    (`match_coordinate: npm/@modelcontextprotocol/server-filesystem`), not the
    local config alias (`fs`), demonstrating the coordinate-keyed curated
    lookup against the real `capabilities/` corpus.
    """
    from tools.bom_cli import main as bom_main

    skill_dir = tmp_path / ".claude" / "skills" / "bashful"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bashful\ndescription: runs bash\nallowed-tools: Bash\n---\nbody\n",
        encoding="utf-8",
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fs": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(bom_main, ["repo", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)

    by_type = {}
    for component in doc["components"]:
        props = _props_by_name(component)
        by_type[props.get("openaca:component_type")] = props

    skill_props = by_type["skill"]
    assert skill_props["openaca:capability_coverage"] == "partial"
    skill_caps = json.loads(skill_props["openaca:capabilities"])
    shell = next(c for c in skill_caps if c["name"] == "shell_exec")
    assert shell["method"] == "declared"
    assert shell["execution_locus"] == "local"

    mcp_props = by_type["mcp_server"]
    assert mcp_props["openaca:capability_coverage"] == "partial"
    mcp_caps = json.loads(mcp_props["openaca:capabilities"])
    curated = {c["name"]: c for c in mcp_caps}
    assert {"file_read", "file_write"} <= set(curated)
    for name in ("file_read", "file_write"):
        assert curated[name]["method"] == "curated"
        assert curated[name]["execution_locus"] == "local"

    meta_props = _props_by_name(doc["metadata"])
    assert meta_props["openaca:schema_version"] == "0.5"


def test_cursor_repo_scan_end_to_end(tmp_path):
    """A repo with Cursor MCP + Skills scans correctly by default: both
    surfaces are found, correctly host-tagged in the BOM, and posture
    rules label them as Cursor, not Claude Code."""
    from tools.cli import main as cli_main
    from tools.graph_build import build_graph

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]},
                    "insecure-api": {"url": "http://example.com/mcp"},
                    "git": {"command": "npx", "args": ["@cyanheads/git-mcp-server@1.1.0"]},
                }
            }
        )
    )
    skill_dir = cursor_dir / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: deploy things\n---\nrun the deploy\n"
    )

    # BOM layer: component discovered, correctly host-tagged.
    # build_agent_bom ignores its positional `refs` argument entirely
    # whenever `graph=` is also passed (it early-returns into
    # _build_agent_bom_from_graph(graph, ...) — confirmed by reading
    # tools/bom.py:138-166) — no need for tools.scan's private
    # _refs_from_graph helper here at all; pass [] for the ignored arg.
    graph = build_graph(tmp_path, mode="repo")  # --host omitted -> every host
    bom = build_agent_bom([], target_type="repo", target=str(tmp_path), graph=graph).to_cyclonedx()
    round_tripped = component_refs_from_cyclonedx(bom)
    weather = next(r for r in round_tripped if r.name == "weather-mcp")
    assert weather.extra["runtime_hosts"] == ["cursor"]
    skill = next(
        r for r in round_tripped if r.name == "deploy" and r.extra.get("component_type") == "skill"
    )
    assert skill.extra["runtime_hosts"] == ["cursor"]

    # CLI/posture layer: the insecure-transport rule labels the finding
    # as Cursor, not Claude Code (Task 9's fix).
    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["scan", "repo", "--target", str(tmp_path), "--include-posture", "--format", "json"],
    )
    doc = _scan_json_doc(result.output)
    posture = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "posture"
        and f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture
    assert posture[0]["active_in"] == ["cursor"]

    # OSV-matching layer: a Cursor-discovered component
    # must flow through vulnerability matching the same as a Claude one,
    # not just BOM/posture. Uses conftest's offline-OSV fixture map
    # (@cyanheads/git-mcp-server@1.1.0 -> GHSA-3q26-f695-pp76, already
    # relied on elsewhere in this file) rather than a live OSV.dev call.
    #
    # Filter by advisory ID, not component.name: finding_to_output()'s
    # component name comes from
    # component_name_for(), which prefers the last component_path entry
    # — the mcpServers dict *key* ("git", this fixture's server alias) —
    # over the package name ("git-mcp-server"), per
    # tools/finding_output.py:25-35. Filtering on "git-mcp-server" would
    # silently match nothing. The advisory ID is unambiguous and doesn't
    # depend on getting the display-name precedence rule right in the
    # test too.
    vuln_result = runner.invoke(
        cli_main,
        ["scan", "repo", "--target", str(tmp_path), "--format", "json"],
    )
    vuln_doc = _scan_json_doc(vuln_result.output)
    vuln_findings = [
        f
        for f in vuln_doc["findings"]
        if f.get("finding_type") == "vulnerability" and f.get("id") == "GHSA-3q26-f695-pp76"
    ]
    assert vuln_findings
    assert vuln_findings[0]["active_in"] == ["cursor"]


def test_cursor_endpoint_scan_end_to_end(tmp_path, monkeypatch):
    """Endpoint-mode Cursor scan through the real CLI: MCP + Skills +
    dev-linked Plugin discovered, host-labeled, no enabled-state asserted
    for the plugin, findings attributed to cursor."""
    from tools.cli import main as cli_main

    # ~/.agents/skills is home-scoped; keep the scan hermetic.
    monkeypatch.setenv("HOME", str(tmp_path))
    cursor_root = tmp_path / "cursor"
    (cursor_root / "skills" / "helper").mkdir(parents=True)
    (cursor_root / "skills" / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: d\n---\nrun\n"
    )
    (cursor_root / "mcp.json").write_text(
        '{"mcpServers": {"git": {"command": "npx", "args": ["@cyanheads/git-mcp-server@1.1.0"]},'
        ' "insecure-api": {"url": "http://insecure.example/mcp"}}}'
    )
    plugin_dir = cursor_root / "plugins" / "local" / "demo" / ".cursor-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "scan",
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
    doc = _scan_json_doc(result.output)

    vuln = [
        f
        for f in doc["findings"]
        if f.get("finding_type") == "vulnerability" and f.get("id") == "GHSA-3q26-f695-pp76"
    ]
    assert vuln and vuln[0]["active_in"] == ["cursor"]
    posture = [
        f for f in doc["findings"] if f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture and posture[0]["active_in"] == ["cursor"]
    assert not [
        f for f in doc["findings"] if f.get("rule_id") == "openaca-posture-mcp-auto-approve"
    ]
    # Cursor's host_surface display label ("Cursor") is how the JSON
    # document names the scanned host at the target level.
    assert doc["target"]["host_surface"] == "Cursor"


def test_cursor_endpoint_marketplace_cached_plugin_scan_end_to_end(tmp_path, monkeypatch):
    """ADR-0045 Decision #7: a marketplace-cached plugin (never dev-linked) is detected
    end to end through the real CLI — presence-only, no enabled-state, with
    its bundled skill and insecure-transport MCP attributed to cursor."""
    from tools.cli import main as cli_main

    monkeypatch.setenv("HOME", str(tmp_path))
    cursor_root = tmp_path / "cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "alpha" / "deadbeef"
    (cached / ".cursor-plugin").mkdir(parents=True)
    (cached / ".cursor-plugin" / "plugin.json").write_text('{"name": "alpha"}')
    (cached / "skills" / "cached-skill").mkdir(parents=True)
    (cached / "skills" / "cached-skill" / "SKILL.md").write_text(
        "---\nname: cached-skill\ndescription: d\n---\nrun\n"
    )
    (cached / "mcp.json").write_text(
        '{"mcpServers": {"insecure-api": {"url": "http://insecure.example/mcp"}}}'
    )
    (cached / ".cache-complete").write_text("")

    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [
            "scan",
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
    doc = _scan_json_doc(result.output)

    posture = [
        f for f in doc["findings"] if f.get("rule_id") == "openaca-posture-insecure-transport"
    ]
    assert posture and posture[0]["active_in"] == ["cursor"]
    assert doc["target"]["host_surface"] == "Cursor"
    # Presence-only plugin + its bundled skill + its bundled MCP server.
    assert doc["stats"]["components"] >= 3


def test_two_host_endpoint_scan_shows_per_host_attribution(tmp_path, monkeypatch):
    """Plan follow-up: a real two-host endpoint scan (Claude Code + Cursor)
    through the CLI shows per-host attribution end to end — the stats line
    breakdown, a host tag on each top-level inventory entry (not on any
    bundled child), and `components_by_host` in the JSON stats object.
    Change B's fix is exercised alongside it: the Cursor plugin's
    location-derived `scope: user` must never render as `[scope=None]`."""
    from tools.cli import main as cli_main

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    claude_root = tmp_path / ".claude"
    (claude_root / "skills" / "foo").mkdir(parents=True)
    (claude_root / "skills" / "foo" / "SKILL.md").write_text(
        "---\nname: foo\ndescription: d\n---\nrun\n"
    )
    (claude_root / "settings.json").write_text("{}")

    cursor_root = tmp_path / ".cursor"
    (cursor_root / "skills" / "bar").mkdir(parents=True)
    (cursor_root / "skills" / "bar" / "SKILL.md").write_text(
        "---\nname: bar\ndescription: d\n---\nrun\n"
    )
    plugin_dir = cursor_root / "plugins" / "local" / "demo" / ".cursor-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')

    runner = CliRunner()

    text_result = runner.invoke(cli_main, ["scan", "endpoint"])
    assert text_result.exit_code == 0, text_result.output
    # Cursor is presence-only (ADR-0045 Decision #7): a selection that includes it must
    # not claim "active plugin", so the tree header says "plugin" here.
    assert "1 plugin, 2 direct components, 2 total components" in text_result.output
    assert "(claude-code: 1, cursor: 1)" in text_result.output
    assert "plugin/demo" in text_result.output
    assert "[cursor] [scope=user]" in text_result.output
    assert "foo [claude-code]" in text_result.output
    assert "bar [cursor]" in text_result.output
    assert "[scope=None]" not in text_result.output

    json_result = runner.invoke(cli_main, ["scan", "endpoint", "--format", "json"])
    assert json_result.exit_code == 0, json_result.output
    doc = _scan_json_doc(json_result.output)
    # `components_by_host` mirrors `stats.components`' population (every ref,
    # including the plugin self ref itself) — not the tree header's
    # plugin-excluded `total components` count above.
    assert doc["stats"]["components_by_host"] == {"claude-code": 1, "cursor": 2}
    assert sum(doc["stats"]["components_by_host"].values()) == doc["stats"]["components"]


def test_two_host_endpoint_scan_shared_subagent_attributes_to_both_hosts(tmp_path, monkeypatch):
    """A `.claude/agents/*.md` subagent with no `.cursor/agents/` override is
    genuinely shared (Cursor's unconditional compatibility read, ADR-0045
    Decision #4): `subagent_precedence.py` tags it `runtime_hosts=["claude-code",
    "cursor"]`. `openaca:runtime_hosts` carries both (ADR-0044
    Decision #2 — there is no derived singular companion property) and the
    render layer must not collapse them into "claude-code" by default: the host
    tag must show both hosts, and `components_by_host` must count the
    subagent under both, not silently drop its Cursor ownership."""
    from tools.cli import main as cli_main

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    claude_root = tmp_path / ".claude"
    (claude_root / "agents").mkdir(parents=True)
    (claude_root / "agents" / "shared.md").write_text(
        "---\nname: shared\ndescription: d\n---\nrun\n"
    )
    (claude_root / "settings.json").write_text("{}")

    cursor_root = tmp_path / ".cursor"
    cursor_root.mkdir(parents=True)

    runner = CliRunner()

    text_result = runner.invoke(cli_main, ["scan", "endpoint"])
    assert text_result.exit_code == 0, text_result.output
    assert "shared [claude-code + cursor]" in text_result.output

    json_result = runner.invoke(cli_main, ["scan", "endpoint", "--format", "json"])
    assert json_result.exit_code == 0, json_result.output
    doc = _scan_json_doc(json_result.output)
    assert doc["stats"]["components_by_host"] == {"claude-code": 1, "cursor": 1}


def test_two_host_endpoint_scan_attributes_bundled_package_deps_by_ancestor(tmp_path, monkeypatch):
    """`_add_dep_manifest_packages` (tools/graph_build.py) never stamps
    `runtime_hosts` on the package refs it emits, for either the plugin-own-
    root-deps call site or the bundled-skill call site — so a Cursor cached
    plugin's bundled skill's own `package.json` dependency, and a Claude
    active plugin's bundled skill's own `package.json` dependency, both
    reach `compute_components_by_host` with no host of their own. This must
    resolve through the graph lineage to the nearest ancestor that DOES carry
    `runtime_hosts` (the bundling skill node in both cases) rather than
    silently defaulting every package to `claude-code` regardless of which
    host's plugin it came from."""
    from tools.cli import main as cli_main

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    claude_root = tmp_path / ".claude"
    claude_plugin_dir = claude_root / "plugins" / "cache" / "market" / "demo" / "1.0.0"
    (claude_plugin_dir / "skills" / "helper").mkdir(parents=True)
    (claude_plugin_dir / "skills" / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: d\n---\nrun\n"
    )
    (claude_plugin_dir / "skills" / "helper" / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "1.0.0"}})
    )
    (claude_root / "plugins").mkdir(parents=True, exist_ok=True)
    (claude_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@market": [
                        {
                            "scope": "user",
                            "installPath": str(claude_plugin_dir),
                            "version": "1.0.0",
                        }
                    ]
                },
            }
        )
    )
    (claude_root / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"demo@market": True}})
    )

    cursor_root = tmp_path / ".cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "alpha" / "deadbeef"
    (cached / ".cursor-plugin").mkdir(parents=True)
    (cached / ".cursor-plugin" / "plugin.json").write_text('{"name": "alpha"}')
    (cached / "skills" / "cached-skill").mkdir(parents=True)
    (cached / "skills" / "cached-skill" / "SKILL.md").write_text(
        "---\nname: cached-skill\ndescription: d\n---\nrun\n"
    )
    (cached / "skills" / "cached-skill" / "package.json").write_text(
        json.dumps({"dependencies": {"right-pad": "1.0.0"}})
    )
    (cached / ".cache-complete").write_text("")

    runner = CliRunner()

    text_result = runner.invoke(cli_main, ["scan", "endpoint"])
    assert text_result.exit_code == 0, text_result.output
    # Tree total-components population excludes the plugin self refs: one
    # skill + one package per host. Cursor is presence-only (ADR-0045 Decision #7), so
    # the label is "plugins", not "active plugins".
    assert "2 plugins, 0 direct components, 4 total components" in text_result.output
    assert "(claude-code: 2, cursor: 2)" in text_result.output

    json_result = runner.invoke(cli_main, ["scan", "endpoint", "--format", "json"])
    assert json_result.exit_code == 0, json_result.output
    doc = _scan_json_doc(json_result.output)
    # JSON population includes the plugin self refs too: plugin + skill +
    # package per host.
    assert doc["stats"]["components_by_host"] == {"claude-code": 3, "cursor": 3}


def test_single_host_endpoint_scan_json_stats_components_by_host_one_key(tmp_path, monkeypatch):
    """`components_by_host` is always present in the JSON stats object (not
    gated on host count), but a single-host scan has exactly one key."""
    from tools.cli import main as cli_main

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    config_dir = REPO_ROOT / "tests" / "fixtures" / "installs" / "minimal"
    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        ["scan", "endpoint", "--config-dir", str(config_dir), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    doc = _scan_json_doc(result.output)
    assert doc["stats"]["components_by_host"] == {"claude-code": doc["stats"]["components"]}


# --- Parser/graph bundle-boundary parity -----------------------------------
#
# parse_repo (flat walk) and build_graph (attributed walk) each decide,
# independently, whether a directory is a foreign plugin-bundle boundary
# whose contents must be excluded for the selected hosts. Five review
# rounds on multi-host support each fixed one walk and regressed the
# other; this matrix pins the invariant itself: for every boundary shape,
# both walks must agree on what gets inventoried.

_PARITY_MCP = '{"mcpServers": {"bundled": {"command": "npx", "args": ["bundled-mcp@1.0.0"]}}}'
_PARITY_NATIVE = '{"name": "native-demo"}'
_PARITY_AGENT_PLUGINS = json.dumps(
    {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "open-demo"}
)


def _parity_fixture(root: Path, manifests: dict[str, str]) -> None:
    """Build `root/bundle` with an inner mcp.json + Claude subagent, plus the
    given manifests: {relative path: "valid"|"malformed"|"escaping"|"broken"}.
    Symlink modes point the manifest outside the bundle (escaping) or at a
    missing target (broken)."""
    import os

    bundle = root / "bundle"
    (bundle / ".claude" / "agents").mkdir(parents=True)
    (bundle / "mcp.json").write_text(_PARITY_MCP)
    (bundle / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\n---\nbody\n")
    for rel, mode in manifests.items():
        target = bundle / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _PARITY_AGENT_PLUGINS if rel == "plugin.json" else _PARITY_NATIVE
        if mode == "valid":
            target.write_text(content)
        elif mode == "malformed":
            target.write_text("{not json")
        elif mode == "escaping":
            outside = root / f"outside-{rel.replace('/', '-')}"
            outside.write_text(content)
            os.symlink(outside, target)
        elif mode == "broken":
            os.symlink(f"/nonexistent/{rel}", target)


def _parity_signals(root: Path, hosts: list[str]) -> dict[str, tuple[bool, bool]]:
    """(parser, graph) inventory booleans for the bundle's mcp server and
    subagent."""
    from tools.graph_build import build_graph
    from tools.parsers import parse_repo

    parser_names = {r.name for r in parse_repo(root, hosts=hosts)}
    graph = build_graph(root, "repo", hosts=hosts)
    graph_kinds = {
        (n.kind, n.ref.name if n.ref is not None else None) for n in graph.nodes.values()
    }
    return {
        "mcp": (
            "bundled-mcp" in parser_names,
            any(k == "mcp_server" for k, _ in graph_kinds),
        ),
        "subagent": (
            "helper" in parser_names,
            ("agent", "helper") in graph_kinds,
        ),
    }


@pytest.mark.parametrize(
    ("manifests", "hosts", "expected"),
    [
        # No foreign manifest: both walks inventory both surfaces.
        ({}, ["claude-code"], {"mcp": True, "subagent": True}),
        # Valid/malformed unselected Cursor bundle: presence alone proves a
        # foreign boundary — both walks exclude the whole subtree.
        (
            {".cursor-plugin/plugin.json": "valid"},
            ["claude-code"],
            {"mcp": False, "subagent": False},
        ),
        (
            {".cursor-plugin/plugin.json": "malformed"},
            ["claude-code"],
            {"mcp": False, "subagent": False},
        ),
        # Escaping/broken symlinked foreign manifest is not a candidate in
        # either walk — no boundary, surfaces stay inventoried.
        (
            {".cursor-plugin/plugin.json": "escaping"},
            ["claude-code"],
            {"mcp": True, "subagent": True},
        ),
        (
            {".cursor-plugin/plugin.json": "broken"},
            ["claude-code"],
            {"mcp": True, "subagent": True},
        ),
        # Dual-manifest bundle where the selected-host sibling can't realize:
        # the valid unselected sibling still proves a foreign boundary.
        (
            {".cursor-plugin/plugin.json": "valid", ".claude-plugin/plugin.json": "malformed"},
            ["claude-code"],
            {"mcp": False, "subagent": False},
        ),
        (
            {".cursor-plugin/plugin.json": "valid", ".claude-plugin/plugin.json": "escaping"},
            ["claude-code"],
            {"mcp": False, "subagent": False},
        ),
        # Selected-host manifest escaping alone: dropped as a candidate, no
        # plugin realizes, surfaces stay target-level in both walks.
        (
            {".claude-plugin/plugin.json": "escaping"},
            ["claude-code"],
            {"mcp": True, "subagent": True},
        ),
        # Agent Plugins bundle under hosts=["claude-code"]: the Agent Plugins
        # contract is cursor-gated, so neither walk treats it as a boundary.
        ({"plugin.json": "valid"}, ["claude-code"], {"mcp": True, "subagent": True}),
        # Escaping/broken Agent Plugins manifest under hosts=["cursor"]: not a
        # candidate, no bundle realizes; the shared subagent stays inventoried
        # (Cursor reads .claude/agents), the bare mcp.json matches no
        # cursor-selected pattern in either walk.
        ({"plugin.json": "escaping"}, ["cursor"], {"mcp": False, "subagent": True}),
        ({"plugin.json": "broken"}, ["cursor"], {"mcp": False, "subagent": True}),
        # Mirror direction: a Claude bundle is the foreign one under
        # hosts=["cursor"].
        ({".claude-plugin/plugin.json": "valid"}, ["cursor"], {"mcp": False, "subagent": False}),
        ({".claude-plugin/plugin.json": "escaping"}, ["cursor"], {"mcp": False, "subagent": True}),
    ],
)
def test_parser_graph_bundle_boundary_parity(tmp_path, manifests, hosts, expected):
    _parity_fixture(tmp_path, manifests)
    signals = _parity_signals(tmp_path, hosts)
    for key, (parser_saw, graph_saw) in signals.items():
        assert parser_saw == graph_saw, (
            f"{key}: parse_repo={parser_saw} but build_graph={graph_saw} "
            f"for manifests={manifests} hosts={hosts}"
        )
        assert parser_saw == expected[key], (
            f"{key}: both walks agree on {parser_saw} but expected {expected[key]} "
            f"for manifests={manifests} hosts={hosts}"
        )


def test_realized_bundle_flat_vs_attributed_split_is_pinned(tmp_path):
    """Known, pre-existing asymmetry (reproduces on main with a Claude-only
    fixture): inside a REALIZED plugin bundle, parse_repo's flat walk still
    inventories the bare mcp.json and .claude/agents subagents at target
    level, while build_graph hands the subtree to the plugin node and only
    inventories plugin-convention surfaces (.mcp.json, agents/) — so neither
    appears in the graph at all. This pin makes any change to either side a
    deliberate decision rather than a silent drift; if you break it on
    purpose, decide which walk is right and update both together."""
    _parity_fixture(tmp_path, {".claude-plugin/plugin.json": "valid"})
    signals = _parity_signals(tmp_path, ["claude-code"])
    assert signals["mcp"] == (True, False)
    assert signals["subagent"] == (True, False)
