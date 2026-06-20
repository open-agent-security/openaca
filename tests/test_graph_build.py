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
