import json
import os

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


def test_repo_plugin_root_with_own_dep_manifest(tmp_path):
    # repo root IS a plugin AND has its own package.json — must not double-parent
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    (tmp_path / "package.json").write_text(
        '{"name":"demo","version":"1","dependencies":{"left-pad":"1.0.0"}}'
    )
    g = build_graph(tmp_path, mode="repo")  # must not raise
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "plugin", "target"]


def test_empty_repo_is_just_target(tmp_path):
    g = build_graph(tmp_path, mode="repo")  # must not raise
    assert [n.kind for n in g.nodes.values()] == ["target"]


def _seed_endpoint_fixture_with_plugin_dep(tmp_path):
    """Endpoint layout where the plugin install path has its OWN package.json
    (a plugin implementation dep) in addition to a bundled skill. Reproduces
    Gap 2: both descend()'s plugin branch and _walk_plugin_implementation_deps
    parse the same package.json."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    install_path = install_root / "cache" / "demo" / "1.0.0"
    install_path.mkdir(parents=True)
    _skill_with_dep(install_path, "skills/deploy")  # bundled skill (lodash dep)
    (install_path / "package.json").write_text(
        '{"name":"demo","version":"1.0.0","dependencies":{"left-pad":"1.0.0"}}'
    )

    settings = {"enabledPlugins": {"demo@mp": True}}
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


def test_endpoint_plugin_own_dep_manifest_no_double_emit(tmp_path):
    install_root, project_root = _seed_endpoint_fixture_with_plugin_dep(tmp_path)
    g = build_graph(install_root, mode="endpoint", project_root=project_root)  # must not raise
    plugin_deps = [
        n for n in g.nodes.values() if n.kind == "package" and "left-pad" in (n.key or "")
    ]
    assert len(plugin_deps) == 1
    assert [n.kind for n in g.lineage(plugin_deps[0])] == ["package", "plugin", "target"]


def _seed_endpoint_fixture_with_manifest_and_lockfile(tmp_path):
    """Endpoint layout where the plugin install path has BOTH a package.json
    (direct dep left-pad@1.0.0) AND a package-lock.json pinning that dep plus a
    transitive dep, alongside a bundled skill. Without the fix, descend()'s
    plugin branch emits a manifest-keyed left-pad node and
    _walk_plugin_implementation_deps emits a lockfile-keyed one — two nodes for
    one direct dep."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    install_path = install_root / "cache" / "demo" / "1.0.0"
    install_path.mkdir(parents=True)
    _skill_with_dep(install_path, "skills/deploy")  # bundled skill (lodash dep)
    (install_path / "package.json").write_text(
        '{"name":"demo","version":"1.0.0","dependencies":{"left-pad":"1.0.0"}}'
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1.0.0"},
                    "node_modules/left-pad": {"version": "1.0.0"},
                    "node_modules/dep-transitive": {"version": "2.0.0"},
                },
            }
        )
    )

    settings = {"enabledPlugins": {"demo@mp": True}}
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


def test_endpoint_plugin_manifest_and_lockfile_dep_is_single_node(tmp_path):
    install_root, project_root = _seed_endpoint_fixture_with_manifest_and_lockfile(tmp_path)
    g = build_graph(install_root, mode="endpoint", project_root=project_root)  # must not raise
    left_pad = [n for n in g.nodes.values() if n.kind == "package" and "left-pad" in (n.key or "")]
    # exactly one node: the lockfile walk is the sole source of the plugin's own
    # deps; no manifest-keyed duplicate.
    assert len(left_pad) == 1
    assert [n.kind for n in g.lineage(left_pad[0])] == ["package", "plugin", "target"]


def test_endpoint_manifest_and_lockfile_bundled_skill_chain_still_works(tmp_path):
    # Same fixture: suppressing the plugin's OWN root deps must NOT suppress a
    # bundled skill's own deps — the skill chain stays intact.
    install_root, project_root = _seed_endpoint_fixture_with_manifest_and_lockfile(tmp_path)
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    lodash = next(n for n in g.nodes.values() if n.kind == "package" and "lodash" in (n.key or ""))
    assert [n.kind for n in g.lineage(lodash)] == ["package", "skill", "plugin", "target"]


def test_endpoint_malformed_installed_plugins_does_not_crash(tmp_path):
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@mp": True}}))
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text("{not valid json")
    project_root = tmp_path / "project"
    project_root.mkdir()
    g = build_graph(install_root, mode="endpoint", project_root=project_root)  # must not raise
    assert any(n.kind == "target" for n in g.nodes.values())


def test_nested_plugin_at_depth(tmp_path):
    base = tmp_path / "packages" / "myplugin"
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text('{"name":"nested","version":"1"}')
    skill = base / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    (skill / "package.json").write_text(
        '{"name":"deploy","version":"1","dependencies":{"lodash":"4.17.20"}}'
    )
    g = build_graph(tmp_path, mode="repo")  # must not raise
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "plugin", "target"]
    # the skill is under the plugin, not a project skill of target:
    skill_node = next(n for n in g.nodes.values() if n.kind == "skill")
    assert g.nearest_plugin_ancestor(skill_node) is not None


def test_nested_plugin_dot_claude_skills_not_project_skill_of_target(tmp_path):
    # A `.claude/skills/` dir INSIDE a plugin subtree must NOT be emitted as a
    # project skill of target — exclude_under covers nested plugin roots, so the
    # target's project-skill walk skips everything beneath the plugin. (Plugins
    # bundle skills under `skills/`, so this `.claude/skills/` form is not a
    # plugin-bundled surface either: the invariant is purely "not a target
    # child", preserving single-parent.)
    base = tmp_path / "packages" / "myplugin"
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text('{"name":"nested","version":"1"}')
    _skill_with_dep(base, ".claude/skills/deploy")
    # also give the plugin a real bundled skill so the plugin node has children
    _skill_with_dep(base, "skills/build")
    g = build_graph(tmp_path, mode="repo")  # must not raise
    skills = [n for n in g.nodes.values() if n.kind == "skill"]
    # only the plugin-bundled `skills/build` skill is discovered; the
    # `.claude/skills/deploy` under the plugin is excluded from the target walk.
    assert len(skills) == 1
    assert g.nearest_plugin_ancestor(skills[0]) is not None
    # no skill is a direct project skill of target
    target = g.root
    assert all(g.nearest_plugin_ancestor(s) is not None for s in skills)
    assert target.key not in {e.parent for e in g.edges if g.nodes[e.child].kind == "skill"}


# --- Task 2.5: lockfiles + bundled non-skill plugin surfaces + repo standalone ---


def test_repo_package_lock_emits_transitive_packages(tmp_path):
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "host", "version": "1.0.0"},
                    "node_modules/left-pad": {"version": "1.0.0"},
                    "node_modules/dep-transitive": {"version": "2.0.0"},
                },
            }
        )
    )
    g = build_graph(tmp_path, mode="repo")
    pkgs = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkgs) == 2
    for p in pkgs:
        assert p.ref is not None
        assert (p.ref.extra or {}).get("transitive") is True
        assert [n.kind for n in g.lineage(p)] == ["package", "target"]


def test_repo_uv_lock_emits_transitive_packages(tmp_path):
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "requests"\nversion = "2.0.0"\n'
    )
    g = build_graph(tmp_path, mode="repo")
    pkgs = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkgs) == 1
    assert pkgs[0].ref is not None
    assert (pkgs[0].ref.extra or {}).get("transitive") is True


def test_repo_lockfile_in_skill_dir_nests_under_skill(tmp_path):
    skill = tmp_path / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    (skill / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "deploy", "version": "1"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "target"]


def test_repo_plugin_lockfile_nests_under_plugin(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "demo", "version": "1"},
                    "node_modules/left-pad": {"version": "1.0.0"},
                },
            }
        )
    )
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "plugin", "target"]


def test_repo_standalone_mcp_manifest_is_target_child(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"git": {"command": "npx", "args": ["@org/git-mcp@1.0.0"]}}})
    )
    g = build_graph(tmp_path, mode="repo")
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "target"]


def test_repo_claude_desktop_config_is_target_child(tmp_path):
    (tmp_path / "claude_desktop_config.json").write_text(
        json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["@mcp/fs"]}}})
    )
    g = build_graph(tmp_path, mode="repo")
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "target"]


def test_repo_commands_and_agents_are_target_children(tmp_path):
    cmd = tmp_path / ".claude" / "commands"
    cmd.mkdir(parents=True)
    (cmd / "deploy.md").write_text("---\nname: deploy\n---\nrun\n")
    agt = tmp_path / ".claude" / "agents"
    agt.mkdir(parents=True)
    (agt / "reviewer.md").write_text("---\nname: reviewer\n---\nreview\n")
    g = build_graph(tmp_path, mode="repo")
    command = next(n for n in g.nodes.values() if n.kind == "command")
    agent = next(n for n in g.nodes.values() if n.kind == "agent")
    assert [n.kind for n in g.lineage(command)] == ["command", "target"]
    assert [n.kind for n in g.lineage(agent)] == ["agent", "target"]


def test_repo_command_inside_plugin_not_double_discovered_by_target(tmp_path):
    base = tmp_path / "packages" / "myplugin"
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text('{"name":"nested","version":"1"}')
    cmd = base / ".claude" / "commands"
    cmd.mkdir(parents=True)
    (cmd / "deploy.md").write_text("---\nname: deploy\n---\nrun\n")
    g = build_graph(tmp_path, mode="repo")  # must not raise
    # The `.claude/commands` dir lives inside the plugin subtree; it must not be
    # emitted as a target-level command (single-parent / exclude_under).
    commands = [n for n in g.nodes.values() if n.kind == "command"]
    target = g.root
    assert target.key not in {e.parent for e in g.edges if g.nodes[e.child].kind == "command"}
    assert all(n.kind == "command" for n in commands) or not commands


def test_repo_plugin_bundled_mcp_and_hooks_are_plugin_children(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"git": {"command": "npx", "args": ["@org/git-mcp@1.0.0"]}}})
    )
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"type": "command", "command": "echo hi"}],
                }
            }
        )
    )
    cmd = tmp_path / "commands"
    cmd.mkdir()
    (cmd / "build.md").write_text("---\nname: build\n---\nbuild\n")
    g = build_graph(tmp_path, mode="repo")
    plugin = next(n for n in g.nodes.values() if n.kind == "plugin")
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    hook = next(n for n in g.nodes.values() if n.kind == "hook")
    command = next(n for n in g.nodes.values() if n.kind == "command")
    assert g.lineage(mcp)[1].key == plugin.key
    assert g.lineage(hook)[1].key == plugin.key
    assert g.lineage(command)[1].key == plugin.key
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "plugin", "target"]


def test_endpoint_plugin_bundled_mcp_is_plugin_child(tmp_path):
    install_root = tmp_path / "claude"
    install_root.mkdir()
    install_path = install_root / "cache" / "demo" / "1.0.0"
    install_path.mkdir(parents=True)
    (install_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"evil": {"command": "npx", "args": ["-y", "@evil/mcp@0.9.0"]}}})
    )
    settings = {"enabledPlugins": {"demo@mp": True}}
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
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "plugin", "target"]


def test_repo_plugin_bundled_skill_not_double_created_by_surface_walk(tmp_path):
    # The plugin bundles a skill (created by descent) AND non-skill surfaces.
    # Adding the non-skill surfaces must not duplicate the skill node.
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    _skill_with_dep(tmp_path, "skills/deploy")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"git": {"command": "npx", "args": ["@org/git-mcp@1.0.0"]}}})
    )
    g = build_graph(tmp_path, mode="repo")
    skills = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skills) == 1
    # skill→package dep chain preserved
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "plugin", "target"]


# --- Task 2.5d: endpoint direct components (skills/commands/agents/hooks) ---


def test_endpoint_direct_skill_under_install_root_is_target_child(tmp_path):
    install_root = tmp_path / "claude"
    install_root.mkdir()
    skill_dir = install_root / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    g = build_graph(install_root, mode="endpoint")
    skill = next(n for n in g.nodes.values() if n.kind == "skill")
    assert [n.kind for n in g.lineage(skill)] == ["skill", "target"]
    # direct: no plugin ancestor
    assert g.nearest_plugin_ancestor(skill) is None


def test_endpoint_direct_command_under_install_root_is_target_child(tmp_path):
    install_root = tmp_path / "claude"
    install_root.mkdir()
    commands_dir = install_root / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "review.md").write_text("---\ndescription: review\n---\nbody\n")
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    g = build_graph(install_root, mode="endpoint")
    command = next(n for n in g.nodes.values() if n.kind == "command")
    assert [n.kind for n in g.lineage(command)] == ["command", "target"]
    assert g.nearest_plugin_ancestor(command) is None


def test_endpoint_direct_skill_and_project_skill_not_double_created(tmp_path):
    # An install-root direct skill AND a project skill with the same name must
    # produce two distinct skill nodes (different occurrences), not collapse or
    # trip the single-parent invariant.
    install_root = tmp_path / "claude"
    install_root.mkdir()
    direct_skill = install_root / "skills" / "deploy"
    direct_skill.mkdir(parents=True)
    (direct_skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    project_skill = project_root / ".claude" / "skills" / "deploy"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    skills = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skills) == 2
    assert all([n.kind for n in g.lineage(s)] == ["skill", "target"] for s in skills)


# --- Codex review fixes ---


def test_repo_plugin_inline_mcp_servers_in_plugin_json(tmp_path):
    """plugin.json with inline mcpServers must add mcp_server children of the plugin."""
    (tmp_path / ".claude-plugin").mkdir()
    plugin_json = {
        "name": "demo",
        "version": "1",
        "mcpServers": {"git": {"command": "npx", "args": ["@org/git-mcp@1.0.0"]}},
    }
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps(plugin_json))
    g = build_graph(tmp_path, mode="repo")
    plugin = next(n for n in g.nodes.values() if n.kind == "plugin")
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "plugin", "target"]
    assert g.lineage(mcp)[1].key == plugin.key


def test_endpoint_standalone_mcp_json_at_project_root(tmp_path):
    """<project>/.mcp.json must produce mcp_server children of the target in endpoint mode."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"git": {"command": "npx", "args": ["@org/git-mcp@1.0.0"]}}})
    )
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "target"]


def test_endpoint_standalone_mcp_json_at_install_root(tmp_path):
    """<install_root>/.mcp.json must produce mcp_server children of the target in endpoint mode."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    (install_root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["@mcp/fs@0.1.0"]}}})
    )
    project_root = tmp_path / "project"
    project_root.mkdir()
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "target"]


def test_repo_settings_json_enabled_plugins_are_plugin_nodes(tmp_path):
    """.claude/settings.json enabledPlugins in repo mode must produce plugin children of target."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"myplugin@marketplace": True}})
    )
    g = build_graph(tmp_path, mode="repo")
    plugins = [n for n in g.nodes.values() if n.kind == "plugin"]
    assert len(plugins) == 1
    assert [n.kind for n in g.lineage(plugins[0])] == ["plugin", "target"]


def test_repo_agent_frontmatter_mcp_is_child_of_agent_not_target(tmp_path):
    """Agent frontmatter mcpServers must become mcp_server children of the agent node,
    not agent-kind siblings under the target."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "my-agent.md").write_text(
        "---\nmcpServers:\n  git:\n    command: npx\n"
        "    args: ['@org/git-mcp@1.0.0']\n---\n# Agent\n"
    )
    g = build_graph(tmp_path, mode="repo")
    g.validate()

    agent_nodes = [n for n in g.nodes.values() if n.kind == "agent"]
    assert len(agent_nodes) == 1
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1

    children_of_agent = g.children_of(agent_nodes[0])
    assert any(n.kind == "mcp_server" for n in children_of_agent), (
        "mcp_server should be a child of the agent node, not the target"
    )
    assert [n.kind for n in g.lineage(mcp_nodes[0])] == ["mcp_server", "agent", "target"]


# --- Stage 4 Codex review fixes ---


def test_endpoint_direct_skill_packages_are_children_of_skill(tmp_path):
    """A direct endpoint skill's dep packages must have skill→target lineage,
    not be missing entirely (bug: _walk_skill_dir returned leaf refs only,
    no descend() into the skill dir)."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    skill_dir = install_root / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    (skill_dir / "package.json").write_text(
        '{"name":"deploy","version":"1","dependencies":{"lodash":"4.17.20"}}'
    )
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    g = build_graph(install_root, mode="endpoint")
    pkg = next((n for n in g.nodes.values() if n.kind == "package"), None)
    assert pkg is not None, "skill dep package must appear in the graph"
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "target"]
    assert g.scope_of(pkg) == "agent-dependency"


def test_endpoint_direct_agent_frontmatter_mcp_is_child_of_agent_not_target(tmp_path):
    """Agent frontmatter mcpServers in endpoint direct agents must become
    mcp_server children of the agent node, not siblings under the target
    (bug: enumerate_dir returned flat refs; all were attached to target)."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    agents_dir = install_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "my-agent.md").write_text(
        "---\nmcpServers:\n  git:\n    command: npx\n"
        "    args: ['@org/git-mcp@1.0.0']\n---\n# Agent\n"
    )
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    g = build_graph(install_root, mode="endpoint")
    g.validate()
    agent_nodes = [n for n in g.nodes.values() if n.kind == "agent"]
    assert len(agent_nodes) == 1
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert [n.kind for n in g.lineage(mcp_nodes[0])] == ["mcp_server", "agent", "target"]
    children_of_agent = g.children_of(agent_nodes[0])
    assert any(n.kind == "mcp_server" for n in children_of_agent)


# --- Stage 4 second Codex review fixes ---


def test_endpoint_plugin_warnings_propagated_from_build_graph(tmp_path):
    """_load_plugins_map warnings (e.g. malformed installed_plugins.json) must
    surface via the warnings= accumulator passed to build_graph, not be
    silently dropped by the graph builder."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    plugins_dir = install_root / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "installed_plugins.json").write_text("this is not valid json{{{{")

    warnings: list[str] = []
    g = build_graph(install_root, mode="endpoint", warnings=warnings)
    g.validate()
    assert any("installed_plugins.json" in w for w in warnings), (
        f"expected a warning about malformed installed_plugins.json, got: {warnings}"
    )


def test_endpoint_direct_skill_source_provenance_stamped(tmp_path):
    """Direct endpoint skills whose SKILL.md appears in a .skill-lock.json
    must carry source_provenance in their ref's extra dict (parity with the
    old _parse_direct_skill path in claude_install)."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    skill_dir = install_root / "skills" / "aws-api"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: aws-api\ndescription: d\n---\nrun\n")
    # .skill-lock.json at install_root (candidate: skills_root.parent/.skill-lock.json)
    (install_root / ".skill-lock.json").write_text(
        json.dumps(
            {
                "skills": {
                    "aws-api": {
                        "source": "https://github.com/user/aws-api-skill",
                        "sourceType": "github",
                        "ref": "abc123",
                    }
                }
            }
        )
    )
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )

    g = build_graph(install_root, mode="endpoint")
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref is not None
    provenance = skill_nodes[0].ref.extra.get("source_provenance")
    assert isinstance(provenance, dict), "source_provenance must be a dict stamped on the skill ref"
    assert provenance.get("status") == "known"
    assert provenance.get("source") == "https://github.com/user/aws-api-skill"
    assert provenance.get("ref") == "abc123"


# --- Stage 5 Codex review fixes ---


def test_endpoint_project_skill_symlink_followed(tmp_path):
    """Project skills at <project>/.claude/skills/<name> that are symlinks to
    another directory must be discovered in endpoint mode.

    Old path: _walk_project_skill_dirs called _walk_skill_dir (Path.iterdir,
    follows symlinks) before the iter_unignored_files walk. New path: must also
    call _add_skills_from_dir (iterdir-based) so symlinked skill dirs are found.
    """
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    project_root.mkdir()

    real_skill_dir = tmp_path / "skills-store" / "aws-api"
    real_skill_dir.mkdir(parents=True)
    (real_skill_dir / "SKILL.md").write_text("---\nname: aws-api\ndescription: d\n---\nrun\n")
    (real_skill_dir / "package.json").write_text(
        '{"name":"aws-api","version":"1","dependencies":{"boto3":"1.34.0"}}'
    )

    skills_dir = project_root / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    os.symlink(real_skill_dir, skills_dir / "aws-api")

    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1, (
        f"Expected 1 skill node for symlinked skill dir, got {len(skill_nodes)}"
    )
    assert [n.kind for n in g.lineage(skill_nodes[0])] == ["skill", "target"]
    pkg_nodes = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkg_nodes) == 1, (
        "package.json inside symlinked skill dir must produce a package node"
    )


def test_repo_bundled_plugin_dep_refs_are_component_nodes(tmp_path):
    """plugin.json 'dependencies' refs pass through _with_plugin_context (which
    stamps component_type='component') before the kind-guard, so they end up as
    'component' kind nodes — not silently dropped.

    Codex review claimed these are skipped; this test proves they are not.
    """
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"demo","version":"1","dependencies":["helper-lib"]}'
    )
    g = build_graph(tmp_path, mode="repo")
    component_nodes = [n for n in g.nodes.values() if n.kind == "component"]
    assert len(component_nodes) == 1, (
        f"Expected 1 'component' node for plugin-dep/helper-lib, got {len(component_nodes)}"
    )
    assert component_nodes[0].ref is not None
    assert component_nodes[0].ref.component_identity == "plugin-dep/helper-lib"
    assert [n.kind for n in g.lineage(component_nodes[0])] == ["component", "plugin", "target"]


def test_repo_gitignored_root_dep_manifest_skipped(tmp_path):
    """A dep manifest at the repo root that is gitignored must not surface
    packages in the graph (parity with parse_repo_grouped which uses
    iter_unignored_files).
    """
    (tmp_path / ".gitignore").write_text("package.json\n")
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"left-pad":"1.0.0"}}'
    )
    g = build_graph(tmp_path, mode="repo")
    pkg_nodes = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkg_nodes) == 0, (
        "gitignored package.json at repo root must not surface package nodes"
    )


def test_repo_root_gitignore_skips_nested_skill_dep_manifest(tmp_path):
    """A nested skill's dep manifest ignored by the SCAN-ROOT .gitignore must not
    surface a package node, while the skill (tracked component) still does.

    Reproduces the Codex P2: descent into the nested skill dir previously loaded
    the SKILL dir's own (absent) .gitignore and evaluated a dir-relative path, so
    a scan-root ignore rule for `.claude/skills/deploy/package.json` was never
    honored. parse_repo_grouped loads the root spec once and evaluates
    root-relative, so it would skip the manifest.
    """
    _skill_with_dep(tmp_path, ".claude/skills/deploy")
    (tmp_path / ".gitignore").write_text(".claude/skills/deploy/package.json\n")

    g = build_graph(tmp_path, mode="repo")
    pkg_nodes = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkg_nodes) == 0, "scan-root gitignore must skip the nested skill's package.json"
    # the skill itself (tracked component manifest) is still discovered
    assert len([n for n in g.nodes.values() if n.kind == "skill"]) == 1

    # include_gitignored=True bypasses all ignore filtering: the dep reappears
    g2 = build_graph(tmp_path, mode="repo", include_gitignored=True)
    pkg_nodes2 = [n for n in g2.nodes.values() if n.kind == "package"]
    assert len(pkg_nodes2) == 1
    assert [n.kind for n in g2.lineage(pkg_nodes2[0])] == ["package", "skill", "target"]


def test_repo_root_gitignore_skips_nested_plugin_dep_manifest(tmp_path):
    """A nested plugin's own dep manifest ignored by the SCAN-ROOT .gitignore
    must not surface a package node, while the plugin (tracked) still does."""
    base = tmp_path / "packages" / "plugin"
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    (base / "package.json").write_text(
        '{"name":"demo","version":"1","dependencies":{"left-pad":"1.0.0"}}'
    )
    (tmp_path / ".gitignore").write_text("packages/plugin/package.json\n")

    g = build_graph(tmp_path, mode="repo")
    assert len([n for n in g.nodes.values() if n.kind == "package"]) == 0, (
        "scan-root gitignore must skip the nested plugin's package.json"
    )
    assert len([n for n in g.nodes.values() if n.kind == "plugin"]) == 1

    g2 = build_graph(tmp_path, mode="repo", include_gitignored=True)
    pkg_nodes2 = [n for n in g2.nodes.values() if n.kind == "package"]
    assert len(pkg_nodes2) == 1
    assert [n.kind for n in g2.lineage(pkg_nodes2[0])] == ["package", "plugin", "target"]


def test_repo_non_ignored_nested_dep_still_discovered(tmp_path):
    """No over-filtering: a scan-root .gitignore that ignores something else must
    NOT suppress a nested skill's dep manifest."""
    _skill_with_dep(tmp_path, ".claude/skills/deploy")
    (tmp_path / ".gitignore").write_text("dist/\nnode_modules/\n")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "target"]


def test_plugin_skill_symlink_escape_rejected(tmp_path):
    """A plugin's skills/ entry that symlinks outside the plugin root must be
    rejected, mirroring the escape check in claude_plugin_root._parse_bundled_skills
    (subdir_resolved.is_relative_to(plugin_root_resolved))."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "1.0.0"}'
    )
    (plugin_root / "skills").mkdir()

    outside_skill = tmp_path / "outside-skills" / "bad-skill"
    outside_skill.mkdir(parents=True)
    (outside_skill / "SKILL.md").write_text("---\nname: bad-skill\ndescription: d\n---\nrun\n")

    os.symlink(outside_skill, plugin_root / "skills" / "bad-skill")

    g = build_graph(tmp_path, mode="repo")
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 0, (
        "plugin skill entry symlinking outside the plugin root must be rejected"
    )
    assert len([n for n in g.nodes.values() if n.kind == "plugin"]) == 1


def test_plugin_skill_inside_plugin_root_accepted(tmp_path):
    """A plugin's skills/ entry that is a legitimate symlink within the plugin
    root (e.g. a relative symlink) is accepted — the bounds check is not too strict."""
    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "1.0.0"}'
    )
    real_skill = plugin_root / "bundled-skills" / "good-skill"
    real_skill.mkdir(parents=True)
    (real_skill / "SKILL.md").write_text("---\nname: good-skill\ndescription: d\n---\nrun\n")
    skills_dir = plugin_root / "skills"
    skills_dir.mkdir()
    os.symlink(real_skill, skills_dir / "good-skill")

    g = build_graph(tmp_path, mode="repo")
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1, "plugin skill symlink within the plugin root must be accepted"
