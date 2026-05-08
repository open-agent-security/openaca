from pathlib import Path

from tools.parsers import parse_repo

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_parse_repo_combines_all_manifests():
    refs = []
    for sample in ["sample-npm", "sample-mcp", "sample-plugin", "sample-settings"]:
        refs += parse_repo(REPOS / sample)

    purls = {r.purl for r in refs if r.purl}
    identities = {r.component_identity for r in refs if r.component_identity}

    assert "pkg:npm/%40cyanheads/git-mcp-server@1.1.0" in purls
    assert "pkg:pypi/weather-mcp@0.5.0" in purls
    assert any(i.startswith("claude-plugin/") for i in identities)
    assert any(i.startswith("mcp-stdio/uvx-unpinned:") for i in identities)


def test_one_malformed_manifest_does_not_abort_scan(tmp_path):
    """A repo with both a broken and a valid manifest should still emit refs."""
    (tmp_path / "package.json").write_text("not valid json")
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"git": {"command": "npx", "args": ["@scope/server@1.2.3"]}}}'
    )
    refs = parse_repo(tmp_path)
    purls = {r.purl for r in refs if r.purl}
    assert "pkg:npm/%40scope/server@1.2.3" in purls
