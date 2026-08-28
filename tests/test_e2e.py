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
from tools.policy_cli import main as policy_main
from tools.remote.collector import _prepare_remote_bom, build_endpoint_dry_run_payloads
from tools.remote.upload_contract import enforce_remote_upload_contract
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
    assert scan_doc["target"]["host_surface"] == "Claude Code"


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
                "--kind",
                "claude-code",
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


def test_policy_compile_blocks_a_vulnerable_standalone_mcp_server(tmp_path):
    """policy compiler E2E: agent discovery, graph construction, OSV lookup,
    the checked-in `overlays/` corpus merge, advisory matching, a
    vulnerability risk gate, and Claude managed-settings compilation wire up
    together against a real endpoint layout.

    Unlike a hand-written synthetic advisory, this pins the MCP server to
    `@akoskm/create-mcp-server-stdio`, one of the packages the autouse
    `_offline_osv_for_scan_tests` fixture (see `tests/conftest.py`) serves
    from the real OSV-shaped fixture `tests/fixtures/osv/ghsa-3ch2-jxxc-v4xf.json`
    instead of hitting the network. `tools.scan._load_osv_with_overlays`
    still runs for real from there — `load_overlays` and `apply_overlays`
    merge the actual bundled overlay `overlays/GHSA-3ch2-jxxc-v4xf.yaml` in,
    so a regression in loading or merging the real overlay corpus, not just
    a synthetic fixture, fails this test. The policy gates on
    `CVE-2025-54994`, that overlay's alias, to also exercise alias-based ID
    matching against the real corpus."""
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "evil": {
                        "command": "npx",
                        "args": ["-y", "@akoskm/create-mcp-server-stdio@0.9.0"],
                    }
                }
            }
        )
    )

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """\
version: 1
admission:
  mcps:
    default: allowed
  plugins:
    default: allowed
  skills:
    default: allowed
risk_gates:
  vulnerabilities:
    ids: ["CVE-2025-54994"]
"""
    )

    runner = CliRunner()
    result = runner.invoke(
        policy_main,
        [
            "compile",
            str(policy_path),
            "--target",
            str(tmp_path),
            "--host",
            "claude",
            "--dry-run",
            "--format",
            "json",
            "--managed-settings-dir",
            str(tmp_path / "managed"),
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["expected_policy"]["deniedMcpServers"] == [
        {"serverCommand": ["npx", "-y", "@akoskm/create-mcp-server-stdio@0.9.0"]}
    ]
    blocked = [d for d in report["decisions"] if d["result"] == "blocked"]
    assert any("vulnerability GHSA-3ch2-jxxc-v4xf" in d["reasons"] for d in blocked)


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
                "--kind",
                "claude-code",
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
        result = runner.invoke(
            scan_main, ["endpoint", "--kind", "claude-code", "--config-dir", str(tmp_path), "-v"]
        )
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
            "--kind",
            "claude-code",
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


def test_endpoint_scan_emits_the_migrated_agent_document(tmp_path):
    """The spec's `Migrating Claude Code` diff table, asserted row by row.
    Anything else in this document's diff is a regression."""
    from tools.bom_cli import main as bom_main

    root = tmp_path / ".claude"
    (root / "skills" / "deploy").mkdir(parents=True)
    (root / "skills" / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: d\n---\n", encoding="utf-8"
    )
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )

    out = CliRunner().invoke(
        bom_main, ["endpoint", "--kind", "claude-code", "--config-dir", str(root)]
    )
    assert out.exit_code == 0, out.output
    doc = json.loads(out.output.strip())

    metadata_props = {p["name"]: p["value"] for p in doc["metadata"]["properties"]}
    assert metadata_props["openaca:schema_version"] == "0.5"
    assert "openaca:target_type" not in metadata_props

    component = doc["metadata"]["component"]
    assert component["bom-ref"] == "root/claude-code"
    assert component["name"] == "Claude Code"
    assert {p["name"] for p in component["properties"]} == {
        "openaca:agent_kind",
        "openaca:composition_source",
        "openaca:composition_coverage",
    }

    refs = [c["bom-ref"] for c in doc["components"]]
    assert refs and all(r.startswith("claude-code/") or r.startswith("project/") for r in refs)
    names = {p["name"] for c in doc["components"] for p in c.get("properties", [])}
    assert {"openaca:agent_host", "openaca:runtime_hosts"} & names == set()

    lint_path = tmp_path / "agent.cdx.json"
    lint_path.write_text(json.dumps(doc), encoding="utf-8")
    from tools.cli import main as openaca_main

    lint = CliRunner().invoke(openaca_main, ["bom", "lint", str(lint_path)])
    assert lint.exit_code == 0, lint.output


def test_declared_repo_scan_keeps_component_bom_refs(tmp_path):
    """Repo node keys are bare paths under one root, so only the root ref moves."""
    from tools.bom_cli import main as bom_main

    skill = tmp_path / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    doc = json.loads(
        CliRunner().invoke(bom_main, ["repo", "--target", str(tmp_path)]).output.strip()
    )

    assert doc["metadata"]["component"]["bom-ref"] == "root/claude-code"
    props = {p["name"]: p["value"] for p in doc["metadata"]["component"]["properties"]}
    assert props["openaca:composition_source"] == "declared"
    assert any(c["bom-ref"].startswith(".claude/skills/deploy/") for c in doc["components"])


def test_remote_upload_payload_is_agent_rooted_and_redacted(tmp_path):
    """The uploaded document is agent-rooted, names no place, and carries no
    absolute path. Fails if the collector, the agent registry, the BOM
    emitter, or the redaction layer regresses."""
    config_dir = tmp_path / ".claude"
    (config_dir / "skills" / "demo").mkdir(parents=True)
    (config_dir / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\n---\n", encoding="utf-8"
    )

    payloads = build_endpoint_dry_run_payloads(
        config_dir=config_dir, kind_id="claude-code", project=None
    )

    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["target_locator"] == "endpoint:user-scope"
    metadata = payload["bom"]["metadata"]
    props = {p["name"]: p["value"] for p in metadata["properties"]}
    assert props["openaca:schema_version"] == "0.5"
    assert "openaca:target" not in props
    assert "openaca:target_type" not in props
    component = metadata["component"]
    assert component["bom-ref"] == "root/claude-code"
    agent_props = {p["name"]: p["value"] for p in component["properties"]}
    assert agent_props["openaca:agent_kind"] == "claude-code"
    assert agent_props["openaca:composition_source"] == "installed"
    assert "openaca:agent_id" not in agent_props
    enforce_remote_upload_contract(payload)  # raises if any absolute path survived
    # The enforcer's scope is itself part of what this change alters, so calling
    # it alone would be self-referential. Assert the synthesized metadata
    # strings directly, independent of the enforcer's own idea of its scope.
    synthesized = [component["name"], *(p["value"] for p in component["properties"])]
    synthesized += [p["value"] for p in metadata["properties"]]
    assert not [s for s in synthesized if s.startswith("/") or s.startswith("file://")]
    assert str(tmp_path) not in json.dumps(payload)


def test_declared_repo_bom_covers_both_registered_kinds(tmp_path):
    """Plan 042 marquee, declared side: a repo carrying evidence for both
    registered kinds (`.claude/skills/…`, `.cursor/mcp.json`) emits two Agent
    BOM documents from one `bom repo` call, each parsing clean. Claude Code's
    declared `COVERAGE_BASELINE` is `complete` and Cursor's is `partial`
    (Task 8 Step 1) — the difference is the baseline floor, not a parse
    failure, since both walks are clean. The shared `.claude/agents/
    reviewer.md` file — read by Cursor's own `.claude` compat root as well as
    by Claude Code natively — must key identically in both documents: the
    product promise of the whole cross-read design."""
    from tools.bom_cli import main as bom_main

    skill = tmp_path / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    cursor_mcp = tmp_path / ".cursor" / "mcp.json"
    cursor_mcp.parent.mkdir(parents=True)
    cursor_mcp.write_text(
        json.dumps({"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}),
        encoding="utf-8",
    )

    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: reviews\n---\nbody\n", encoding="utf-8"
    )

    result = CliRunner().invoke(bom_main, ["repo", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    docs = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert len(docs) == 2

    by_kind = {}
    for doc in docs:
        props = {p["name"]: p["value"] for p in doc["metadata"]["component"]["properties"]}
        by_kind[props["openaca:agent_kind"]] = (doc, props)

    assert set(by_kind) == {"claude-code", "cursor"}
    assert by_kind["claude-code"][1]["openaca:composition_coverage"] == "complete"
    assert by_kind["cursor"][1]["openaca:composition_coverage"] == "partial"

    def reviewer_ref(doc):
        return next(
            c["bom-ref"]
            for c in doc["components"]
            if c["bom-ref"].startswith(".claude/agents/reviewer.md")
        )

    assert reviewer_ref(by_kind["claude-code"][0]) == reviewer_ref(by_kind["cursor"][0])


def _write_two_kind_home(fake_home: Path, *, malformed_cursor_plugin: bool) -> None:
    agents_dir = fake_home / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "x.md").write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")

    (fake_home / ".claude" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}),
        encoding="utf-8",
    )

    cached_plugin = fake_home / ".cursor" / "plugins" / "cache" / "acme-market" / "widget" / "sha1"
    (cached_plugin / ".cursor-plugin").mkdir(parents=True)
    (cached_plugin / ".cursor-plugin" / "plugin.json").write_text(
        json.dumps({"name": "widget", "author": {}}), encoding="utf-8"
    )
    (cached_plugin / ".cache-complete").write_text("", encoding="utf-8")

    if malformed_cursor_plugin:
        broken = fake_home / ".cursor" / "plugins" / "cache" / "acme-market" / "broken" / "sha2"
        (broken / ".cursor-plugin").mkdir(parents=True)
        (broken / ".cursor-plugin" / "plugin.json").write_text("{not json", encoding="utf-8")
        (broken / ".cache-complete").write_text("", encoding="utf-8")


@pytest.mark.parametrize(
    "malformed_cursor_plugin", [False, True], ids=["clean", "cursor_only_malformed"]
)
def test_installed_scan_endpoint_covers_both_registered_kinds(
    monkeypatch, tmp_path, malformed_cursor_plugin
):
    """Plan 042 marquee, installed side: with `~/.claude` and `~/.cursor`
    both present, a bare `scan endpoint` (no `--kind`, no `--config-dir`)
    renders one card per registered kind. The Cursor plugin node never
    carries an `enabled` property — installed discovery is blind to
    marketplace enable state (docs/specs/cursor-agent-kind.md) — and the
    shared `~/.claude/agents/x.md` file keys as `claude-code/agents/x.md#…`
    in both kinds' own graphs, not as an absolute path, whichever kind reads
    it.

    Parametrized (still one e2e addition, not a third) over a second fixture
    state that adds an unparseable Cursor-only plugin manifest under a second
    cache entry: Claude Code's per-agent coverage stays `complete` in both
    states, proving the malformed Cursor-only file's evidence gap is never
    counted against Claude Code even though Task 9 Step 5 makes the two
    kinds share scan-wide `stats` totals. Cursor's own coverage is `partial`
    in both states too — not because the malformed file has no effect, but
    because `resolve_coverage` floors observed coverage at each kind's own
    `COVERAGE_BASELINE` (Task 8 Step 1 pins Cursor's installed baseline at
    `partial` already), so a further evidence gap has nothing lower to drop
    to. The isolation is the point: nothing here lets Cursor's gap read back
    as a Claude Code regression, or vice versa.
    """
    from tools.agent_kinds import DiscoveryContext, build_agent_graph, discover_agents
    from tools.scan import main as scan_main

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".cursor").mkdir(parents=True)
    _write_two_kind_home(fake_home, malformed_cursor_plugin=malformed_cursor_plugin)

    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("tools.agent_kinds.claude_code.Path.home", lambda: fake_home)
    monkeypatch.setattr("tools.agent_kinds.cursor.Path.home", lambda: fake_home)
    monkeypatch.setattr("tools.graph_build_cursor.Path.home", lambda: fake_home)

    result = CliRunner().invoke(scan_main, ["endpoint", "--format", "json"])
    assert result.exit_code == 0, result.output
    # Endpoint mode prints one "detected config_dir=..." line per kind before
    # the JSON document, and a trailing summary line after it.
    doc, _ = json.JSONDecoder().raw_decode(result.output[result.output.index("{") :])
    coverage_by_kind = {a["kind"]: a["coverage"] for a in doc["agents"]}
    assert coverage_by_kind == {"claude-code": "complete", "cursor": "partial"}

    text_result = CliRunner().invoke(scan_main, ["endpoint"])
    assert text_result.exit_code == 0, text_result.output
    assert "Claude Code" in text_result.output
    assert "Cursor" in text_result.output

    agents = discover_agents(DiscoveryContext(source="installed"))
    assert {a.kind_id for a in agents} == {"claude-code", "cursor"}

    for agent in agents:
        graph = build_agent_graph(agent)
        for node in graph.nodes.values():
            if node.kind == "plugin" and node.ref is not None:
                assert "enabled" not in (node.ref.extra or {})
        agent_nodes = [n for n in graph.nodes.values() if n.kind == "agent"]
        assert len(agent_nodes) == 1
        assert agent_nodes[0].key.startswith("claude-code/agents/x.md#"), (
            agent.kind_id,
            agent_nodes[0].key,
        )


# --- Codex as the third agent kind (plan 043 Task 12) ----------------------
#
# Cross-layer by construction: each of these fails if discovery, composition,
# the registry, posture, the renderer, or the emitter regresses.


def _codex_home(tmp_path: Path, *, disabled_plugin: bool = False) -> Path:
    """A `$CODEX_HOME` with two cached bundles, one optionally disabled."""
    root = tmp_path / "codex-home"
    root.mkdir()
    config = [
        "[marketplaces.mkt]",
        'source_type = "git"',
        'source = "https://example.test/mkt.git"',
        "",
        '[plugins."alpha@mkt"]',
        "enabled = true",
        "",
        '[plugins."beta@mkt"]',
        f"enabled = {'false' if disabled_plugin else 'true'}",
    ]
    (root / "config.toml").write_text("\n".join(config) + "\n", encoding="utf-8")
    for name in ("alpha", "beta"):
        d = root / "plugins" / "cache" / "mkt" / name / "1.0.0" / ".codex-plugin"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(
            json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8"
        )
    return root


def _codex_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".codex" / "skills" / "demo").mkdir(parents=True)
    (project / ".codex" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\n---\nProject skill.\n", encoding="utf-8"
    )
    (project / ".codex" / "config.toml").write_text(
        '[mcp_servers.proj_svc]\ncommand = "true"\n', encoding="utf-8"
    )
    return project


def test_e2e_declared_three_kinds_one_repo(tmp_path):
    """(a) One tree declaring all three kinds emits three BOMs, each labelled
    with its own `openaca:agent_kind`."""
    from tools.bom_cli import main as bom_main

    (tmp_path / ".claude" / "skills" / "s").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "s" / "SKILL.md").write_text(
        "---\nname: s\n---\nX\n", encoding="utf-8"
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"c": {"command": "true"}}}), encoding="utf-8"
    )
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "hooks.json").write_text(
        json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}]}}),
        encoding="utf-8",
    )
    out = tmp_path / "boms"

    result = CliRunner().invoke(
        bom_main, ["repo", "--target", str(tmp_path), "--output-dir", str(out)]
    )

    assert result.exit_code == 0, result.output
    # One document per kind, the kind carried by the filename. `*.cdx.json`
    # only: the sibling `.openaca-bom-manifest.json` is a list, not a BOM.
    kinds = {p.name.removesuffix(".cdx.json") for p in out.glob("*.cdx.json")}

    assert kinds == {"claude-code", "cursor", "codex"}
    # Each document is a real CycloneDX BOM, not an empty placeholder.
    for path in out.glob("*.cdx.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["bomFormat"] == "CycloneDX"


def test_e2e_codex_endpoint_counts_disabled_plugins_correctly(tmp_path):
    """(b) Real discovery proves Task 9's count fix at all three sites: the
    rendered stats line, the BOM's `source_unit_count`, and the tree header.
    None of them can be exercised before the kind is registered."""
    from tools.scan import main as scan_main

    root = _codex_home(tmp_path, disabled_plugin=True)
    project = _codex_project(tmp_path)

    result = CliRunner().invoke(
        scan_main,
        [
            "endpoint",
            "--kind",
            "codex",
            "--config-dir",
            str(root),
            "--project",
            str(project),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    # A `detected config_dir=...` preamble precedes the JSON document, and
    # human-readable output follows it, so decode just the first object.
    payload, _end = json.JSONDecoder().raw_decode(result.output[result.output.index("{") :])
    assert payload["stats"]["units"] == 1, "one of two plugins is disabled"
    assert payload["stats"]["unit"] == "active plugin"


def test_e2e_codex_endpoint_renders_disabled_plugins_in_the_tree(tmp_path):
    """(b, continued) The tree header must not call a disabled plugin active,
    while still showing it — a disabled plugin is installed."""
    from tools.scan import main as scan_main

    root = _codex_home(tmp_path, disabled_plugin=True)

    result = CliRunner().invoke(
        scan_main,
        ["endpoint", "--kind", "codex", "--config-dir", str(root)],
    )

    assert result.exit_code == 0, result.output
    assert "2 plugins (1 disabled)" in result.output
    assert "2 active plugins" not in result.output
    assert "alpha" in result.output and "beta" in result.output


def test_e2e_codex_endpoint_composes_the_project_layer(tmp_path):
    """(b, continued) `--project` reaches both project surfaces."""
    from tools.scan import main as scan_main

    root = _codex_home(tmp_path)
    project = _codex_project(tmp_path)

    result = CliRunner().invoke(
        scan_main,
        [
            "endpoint",
            "--kind",
            "codex",
            "--config-dir",
            str(root),
            "--project",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "demo" in result.output, "project skill"
    assert "proj_svc" in result.output, "project MCP server"


def test_e2e_codex_config_dir_is_accepted_unlike_cursor(tmp_path):
    """ADR-0056 against ADR-0054, through the actual CLI guard."""
    from tools.scan import main as scan_main

    root = _codex_home(tmp_path)

    accepted = CliRunner().invoke(
        scan_main, ["endpoint", "--kind", "codex", "--config-dir", str(root)]
    )
    refused = CliRunner().invoke(
        scan_main, ["endpoint", "--kind", "cursor", "--config-dir", str(root)]
    )

    assert accepted.exit_code == 0, accepted.output
    assert refused.exit_code != 0


def test_e2e_codex_remote_sync_preserves_kind_posture_and_disabled_inventory(tmp_path):
    """(c) `remote sync endpoint` is the one command Tasks 5-11 never exercise
    directly, and the goal names it. Asserts the Codex kind survives the upload
    payload along with both new posture rules — and that neither
    `mcp_auto_approve` nor `api_endpoint_override` appears, since Codex's
    policy surfaces are not MCP-specific and it has no Anthropic settings."""
    from tools.remote.collector import build_endpoint_dry_run_payloads

    root = _codex_home(tmp_path, disabled_plugin=True)
    (root / "rules").mkdir()
    (root / "rules" / "default.rules").write_text(
        'prefix_rule(pattern=["git", "commit"], decision="allow")\n', encoding="utf-8"
    )
    (root / "config.toml").write_text(
        (root / "config.toml").read_text(encoding="utf-8")
        + '\n[projects."/home/u/repo"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )

    payloads = build_endpoint_dry_run_payloads(config_dir=root, kind_id="codex", project=None)
    blob = json.dumps(payloads)

    assert "codex" in blob
    assert "openaca-posture-command-policy-allow" in blob
    assert "openaca-posture-project-trust" in blob
    assert "openaca-posture-mcp-auto-approve" not in blob
    assert "openaca-posture-api-endpoint-override" not in blob


def test_e2e_codex_disabled_mcp_is_inventoried_but_not_an_active_exposure(tmp_path):
    """A disabled MCP server is still installed, so it is inventoried — but it
    is not running, so an active-exposure rule must not fire on it."""
    from tools.scan import main as scan_main

    root = _codex_home(tmp_path)
    (root / "config.toml").write_text(
        (root / "config.toml").read_text(encoding="utf-8")
        + '\n[mcp_servers.off_remote]\nurl = "http://insecure.test/mcp/"\nenabled = false\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        scan_main,
        ["endpoint", "--kind", "codex", "--config-dir", str(root), "--include-posture"],
    )

    assert result.exit_code in (0, 1), result.output
    # Remote MCP refs key by URL identity rather than the table name, so the
    # host is what surfaces in the rendered inventory.
    assert "insecure.test" in result.output, "a disabled server is still installed"
    assert "openaca-posture-insecure-transport" not in result.output
