from pathlib import Path

from tools.graph_build import build_graph
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
    assert "pkg:pypi/sketchy-mcp" in purls
    assert any(i.startswith("plugin/") for i in identities)


def test_one_malformed_manifest_does_not_abort_scan(tmp_path):
    """A repo with both a broken and a valid manifest should still emit refs."""
    (tmp_path / "package.json").write_text("not valid json")
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"git": {"command": "npx", "args": ["@scope/server@1.2.3"]}}}'
    )
    refs = parse_repo(tmp_path)
    purls = {r.purl for r in refs if r.purl}
    assert "pkg:npm/%40scope/server@1.2.3" in purls


def test_dep_manifest_without_plugin_marker_classified_as_software(tmp_path):
    """A bare package.json (no .claude-plugin/plugin.json sibling) → its deps
    are software-dependency. Scope now comes from the composition graph
    (`Graph.scope_of`), not a path heuristic in the parser — `parse_repo`
    itself no longer classifies scope."""
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}'
    )
    graph = build_graph(tmp_path, mode="repo")
    scopes = {graph.scope_of(n) for n in graph.nodes.values() if n.ref and n.ref.ecosystem == "npm"}
    assert scopes == {"software-dependency"}


def test_dep_manifest_co_located_with_plugin_classified_as_agent_dep(tmp_path):
    """The same package.json but with a sibling .claude-plugin/plugin.json
    becomes agent-dependency — its deps hang off the plugin node in the graph,
    so `scope_of` sees a plugin ancestor."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"my-plugin","version":"1.0.0"}'
    )
    (tmp_path / "package.json").write_text(
        '{"name":"my-plugin","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}'
    )
    graph = build_graph(tmp_path, mode="repo")
    npm_scopes = {
        graph.scope_of(n) for n in graph.nodes.values() if n.ref and n.ref.ecosystem == "npm"
    }
    assert npm_scopes == {"agent-dependency"}
    # The plugin self-identity node stays agent-component.
    cp_scopes = {
        graph.scope_of(n)
        for n in graph.nodes.values()
        if n.ref and (n.ref.extra or {}).get("component_type") == "plugin"
    }
    assert cp_scopes == {"agent-component"}
