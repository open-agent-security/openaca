from pathlib import Path

from tools.parsers.package_json import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_parses_dependencies_and_devDependencies():
    refs = parse(REPOS / "sample-npm" / "package.json")
    purls = {r.purl for r in refs}
    assert "pkg:npm/%40cyanheads/git-mcp-server@1.1.0" in purls
    assert "pkg:npm/mcp-remote@^0.4.2" in purls
    assert "pkg:npm/typescript@^5.0.0" in purls


def test_emits_source_metadata():
    refs = parse(REPOS / "sample-npm" / "package.json")
    by_name = {r.name: r for r in refs}
    cyanheads = by_name["@cyanheads/git-mcp-server"]
    assert cyanheads.source_manifest.endswith("package.json")
    assert cyanheads.source_locator == "dependencies"
    assert by_name["typescript"].source_locator == "devDependencies"
