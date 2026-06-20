import json

from tools.graph_build import build_graph


def test_bare_repo_package_is_software_dependency(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"left-pad":"1.0.0"}}'
    )
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert g.scope_of(pkg) == "software-dependency"
    assert g.lineage(pkg)[-1].kind == "target"


def _skill_with_dep(root, rel):
    d = root / rel
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    (d / "package.json").write_text(
        '{"name":"deploy","version":"1","dependencies":{"lodash":"4.17.20"}}'
    )
    return d


def test_claude_skills_layout(tmp_path):
    _skill_with_dep(tmp_path, ".claude/skills/deploy")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert g.scope_of(pkg) == "agent-dependency"
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "target"]


def test_plugin_bundled_skill_layout(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    _skill_with_dep(tmp_path, "skills/deploy")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "plugin", "target"]


def test_two_skills_same_purl_are_two_nodes(tmp_path):
    _skill_with_dep(tmp_path, ".claude/skills/a")
    _skill_with_dep(tmp_path, ".claude/skills/b")
    g = build_graph(tmp_path, mode="repo")
    pkgs = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkgs) == 2  # same purl, two occurrences, two nodes


def test_nested_project_skill_found(tmp_path):
    _skill_with_dep(tmp_path, "packages/frontend/.claude/skills/ui")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "target"]


def test_plugin_custom_skill_dir_path(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"demo","version":"1","skills":"./extras/skills/"}'
    )
    _skill_with_dep(tmp_path, "extras/skills/deploy")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "plugin", "target"]


def _seed_endpoint_fixture(tmp_path):
    """Endpoint layout: an active plugin whose install path bundles a skill that
    bundles a `lodash` dep, plus a remote MCP declared in settings."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    install_path = install_root / "cache" / "demo" / "1.0.0"
    install_path.mkdir(parents=True)
    _skill_with_dep(install_path, "skills/deploy")  # skill bundling lodash

    settings = {
        "enabledPlugins": {"demo@mp": True},
        "mcpServers": {"weather": {"url": "https://mcp.example.com/sse"}},
    }
    (install_root / "settings.json").write_text(json.dumps(settings))
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@mp": [
                        {"scope": "user", "version": "1.0.0", "installPath": str(install_path)}
                    ]
                },
            }
        )
    )

    project_root = tmp_path / "project"
    project_root.mkdir()
    return install_root, project_root


def test_endpoint_active_plugin_chain(tmp_path):
    install_root, project_root = _seed_endpoint_fixture(tmp_path)
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    pkg = next(n for n in g.nodes.values() if n.kind == "package" and "lodash" in (n.key or ""))
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "plugin", "target"]


def test_endpoint_remote_mcp_is_direct_child_of_target(tmp_path):
    install_root, project_root = _seed_endpoint_fixture(tmp_path)
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "target"]
