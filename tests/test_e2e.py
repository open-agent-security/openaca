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
    (target / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["aws-mcp-server==0.3.0"]\n',
        encoding="utf-8",
    )
    sarif_path = tmp_path / "out.sarif"

    runner = CliRunner()
    result = runner.invoke(
        scan_main,
        [
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
