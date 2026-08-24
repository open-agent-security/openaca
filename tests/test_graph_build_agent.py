from tools.agent_kinds import DiscoveryContext, build_agent_graph, discover_agents
from tools.graph_build import build_graph


def _endpoint_fixture(root):
    skill = root / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh@1.0.0"]}}}',
        encoding="utf-8",
    )
    return root


def test_agent_graph_matches_the_legacy_graph_with_relabelled_keys(tmp_path):
    root = _endpoint_fixture(tmp_path / ".claude")
    legacy = build_graph(root, mode="endpoint")
    agent = discover_agents(DiscoveryContext(source="installed", config_dir=root))[0]

    graph = build_agent_graph(agent)

    assert graph.root.key == "root/claude-code"
    assert graph.root.kind == "target"
    relabelled = {
        k.replace("endpoint/", "claude-code/", 1) if k.startswith("endpoint/") else k
        for k in legacy.nodes
        if k != legacy.root.key
    }
    assert {k for k in graph.nodes if k != graph.root.key} == relabelled
    assert len(graph.edges) == len(legacy.edges)


def test_legacy_endpoint_mode_is_unchanged(tmp_path):
    root = _endpoint_fixture(tmp_path / ".claude")

    legacy = build_graph(root, mode="endpoint")

    assert legacy.root.key == "openaca:target"
    assert any(k.startswith("endpoint/") for k in legacy.nodes)
    assert not any(k.startswith("claude-code/") for k in legacy.nodes)


def test_installed_agent_with_no_configuration_builds_an_empty_graph(tmp_path):
    root = tmp_path / ".claude"
    root.mkdir()
    agent = discover_agents(DiscoveryContext(source="installed", config_dir=root))[0]

    graph = build_agent_graph(agent)

    assert list(graph.nodes) == ["root/claude-code"]
    assert graph.edges == []
