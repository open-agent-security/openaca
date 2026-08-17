import json
import os
from pathlib import Path

import pytest

from tools.component_ref import ComponentRef
from tools.graph_build import build_graph, build_manifest_name_index


def test_manifest_name_index(tmp_path):
    (tmp_path / "pkg-a").mkdir()
    (tmp_path / "pkg-b").mkdir()
    (tmp_path / "pkg-a" / "package.json").write_text('{"name": "@x/a"}')
    (tmp_path / "pkg-b" / "pyproject.toml").write_text('[project]\nname = "b-tool"\n')
    idx = build_manifest_name_index(tmp_path)
    assert idx[("npm", "@x/a")] == (tmp_path / "pkg-a").resolve()
    assert idx[("PyPI", "b-tool")] == (tmp_path / "pkg-b").resolve()


def test_manifest_name_index_normalizes_pypi_names(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "pyproject.toml").write_text('[project]\nname = "My_MCP.tool"\n')
    idx = build_manifest_name_index(tmp_path)
    assert idx[("PyPI", "my-mcp-tool")] == (tmp_path / "pkg").resolve()


def _find_packages(g):
    return [n for n in g.nodes.values() if n.kind == "package"]


def test_mcp_npx_self_launch_reparents_root_deps(tmp_path):
    # DesktopCommander shape: a subdir plugin declares an MCP server launched via
    # `npx <root-package-name>`; the root package.json declares the deps. After
    # ADR-0039 resolution those deps hang off the mcp_server node (re-parented
    # from target), are agent-dependency, and the single-parent invariant holds.
    plugin_dir = tmp_path / "plugins" / "claude" / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    plugin_dir.joinpath("plugin.json").write_text(
        json.dumps(
            {
                "name": "desktop-commander",
                "mcpServers": {
                    "desktop-commander": {
                        "command": "npx",
                        "args": ["-y", "@acme/desktop-commander@latest"],
                    }
                },
            }
        )
    )
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "@acme/desktop-commander",
                "version": "1.0.0",
                "dependencies": {"left-pad": "1.0.0"},
            }
        )
    )
    g = build_graph(tmp_path, mode="repo")
    g.validate()  # no double-parent
    pkgs = [n for n in _find_packages(g) if n.ref and n.ref.name == "left-pad"]
    assert pkgs, "root dep should be present"
    parent_of = g._parent_of()
    for pkg in pkgs:
        assert g.nodes[parent_of[pkg.key]].kind == "mcp_server"
        assert g.scope_of(pkg) == "agent-dependency"


def test_mcp_remote_url_attaches_no_deps(tmp_path):
    plugin_dir = tmp_path / "plugins" / "claude" / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    plugin_dir.joinpath("plugin.json").write_text(
        json.dumps(
            {
                "name": "remote-thing",
                "mcpServers": {"remote-thing": {"url": "https://mcp.example.com/mcp"}},
            }
        )
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "@acme/whatever", "dependencies": {"left-pad": "1.0.0"}})
    )
    g = build_graph(tmp_path, mode="repo")
    g.validate()
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert g.children_of(mcp) == []


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
    skill = next(n for n in g.nodes.values() if n.kind == "skill")
    assert skill.ref is not None
    assert skill.ref.component_identity is None


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
    # Option B: both packages share identity `package/npm/lodash`; the node keys
    # stay distinct via source_manifest#locator (occurrence identity), so dropping
    # parent-qualification from canonical_component_identity does NOT collide them.
    assert pkgs[0].key != pkgs[1].key


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


def test_endpoint_plugin_private_skill_uses_marketplace_namespace(tmp_path):
    install_root, project_root = _seed_endpoint_fixture(tmp_path)

    graph = build_graph(install_root, mode="endpoint", project_root=project_root)

    plugin = next(node for node in graph.nodes.values() if node.kind == "plugin")
    skill = next(node for node in graph.nodes.values() if node.kind == "skill")
    remote_mcp = next(node for node in graph.nodes.values() if node.kind == "mcp_server")
    assert plugin.ref is not None
    assert skill.ref is not None
    assert remote_mcp.ref is not None
    assert plugin.ref.component_identity == "plugin/mp/demo"
    assert skill.ref.component_identity == "skill/plugin/mp/demo/deploy"
    assert remote_mcp.ref.component_identity == "mcp-remote/mcp.example.com/sse"


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


def test_endpoint_project_mcp_npx_self_match_resolves_under_project_root(tmp_path):
    # ADR-0039 (name-match, endpoint): a project-scope MCP that npx-launches the
    # project's own published package resolves to the PROJECT manifest (not
    # install_root), so its deps attach under the MCP node. Exercises the
    # project_root scan_root path with the name-match contract.
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    project_root = tmp_path / "project"
    cdir = project_root / ".claude"
    cdir.mkdir(parents=True)
    (cdir / "settings.json").write_text(
        json.dumps(
            {"mcpServers": {"proj-mcp": {"command": "npx", "args": ["-y", "@proj/mcp@latest"]}}}
        )
    )
    (project_root / "package.json").write_text(
        json.dumps({"name": "@proj/mcp", "dependencies": {"left-pad": "1.0.0"}})
    )
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    g.validate()
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    pkgs = [c for c in g.children_of(mcp) if c.kind == "package"]
    assert any("left-pad" in (p.key or "") for p in pkgs), [p.key for p in pkgs]


def test_endpoint_project_mcp_local_path_attaches_nothing(tmp_path):
    # Phase-1 boundary: a local-path MCP launch is NOT resolved (deferred to
    # Phase 2 cache resolution), so no deps attach — declining beats guessing.
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    project_root = tmp_path / "project"
    cdir = project_root / ".claude"
    cdir.mkdir(parents=True)
    (cdir / "settings.json").write_text(
        json.dumps(
            {"mcpServers": {"local-mcp": {"command": "node", "args": ["./server/index.js"]}}}
        )
    )
    server_dir = project_root / "server"
    server_dir.mkdir()
    (server_dir / "index.js").write_text("//")
    (server_dir / "package.json").write_text(
        json.dumps({"name": "proj", "dependencies": {"left-pad": "1.0.0"}})
    )
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    g.validate()
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [c for c in g.children_of(mcp) if c.kind == "package"] == []


def test_endpoint_project_mcp_name_match_uses_project_manifest(tmp_path):
    # Finding 3: project_root package.json name must be in the name index so a
    # project-scoped MCP declaring `npx @acme/server` resolves to local deps.
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "package.json").write_text(
        json.dumps({"name": "@acme/server", "dependencies": {"left-pad": "1.0.0"}})
    )
    cdir = project_root / ".claude"
    cdir.mkdir()
    (cdir / "settings.json").write_text(
        json.dumps({"mcpServers": {"acme": {"command": "npx", "args": ["@acme/server"]}}})
    )
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    g.validate()
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    pkgs = [c for c in g.children_of(mcp) if c.kind == "package"]
    assert any("left-pad" in (p.key or "") for p in pkgs), [p.key for p in pkgs]


def test_endpoint_direct_mcp_does_not_match_cached_plugin_manifest(tmp_path):
    # ADR-0039 endpoint review: a direct/external settings `npx @external/mcp`
    # must NOT name-match an unrelated installed plugin under plugins/cache/ with
    # the same name and attach its deps (false advisories). The cache subtree is
    # excluded from the name index.
    install_root = tmp_path / "claude"
    (install_root / "plugins" / "cache" / "some-plugin" / "1.0.0").mkdir(parents=True)
    (install_root / "settings.json").write_text(
        json.dumps(
            {"mcpServers": {"ext": {"command": "npx", "args": ["-y", "@external/mcp@latest"]}}}
        )
    )
    (install_root / "plugins" / "cache" / "some-plugin" / "1.0.0" / "package.json").write_text(
        json.dumps({"name": "@external/mcp", "dependencies": {"left-pad": "1.0.0"}})
    )
    project_root = tmp_path / "project"
    project_root.mkdir()
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    g.validate()
    ext = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [c for c in g.children_of(ext) if c.kind == "package"] == []


def test_endpoint_direct_mcp_does_not_match_root_cache_install(tmp_path):
    # P2 fix (Codex): the existing filter only excluded `plugins/cache/...`.
    # Endpoint installs also use `cache/<plugin>/<version>/...` (the installPath
    # written to installed_plugins.json). A direct `npx <pkg>` MCP must NOT
    # name-match a cached plugin at that root-level cache path.
    install_root = tmp_path / "claude"
    install_path = install_root / "cache" / "@external" / "mcp" / "1.0.0"
    install_path.mkdir(parents=True)
    (install_path / "package.json").write_text(
        json.dumps({"name": "@external/mcp", "dependencies": {"left-pad": "1.0.0"}})
    )
    (install_root / "settings.json").write_text(
        json.dumps(
            {"mcpServers": {"ext": {"command": "npx", "args": ["-y", "@external/mcp@latest"]}}}
        )
    )
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    project_root.mkdir()
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    g.validate()
    ext = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [c for c in g.children_of(ext) if c.kind == "package"] == []


def test_endpoint_mcp_does_not_match_gitignored_project_manifest(tmp_path):
    # P2 fix (Codex): build_manifest_name_index was called with
    # include_gitignored=True for project_root in endpoint mode, disabling
    # .gitignore filtering. A direct `npx <pkg>` MCP must NOT resolve to a
    # project-root package.json that is gitignored.
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text(
        json.dumps({"mcpServers": {"ext": {"command": "npx", "args": ["-y", "my-pkg"]}}})
    )
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    ignored_dir = project_root / "ignored"
    ignored_dir.mkdir(parents=True)
    (ignored_dir / "package.json").write_text(
        json.dumps({"name": "my-pkg", "dependencies": {"left-pad": "1.0.0"}})
    )
    # Gitignore the dir so the manifest must be excluded from the name index.
    (project_root / ".gitignore").write_text("ignored/\n")
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    g.validate()
    ext = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [c for c in g.children_of(ext) if c.kind == "package"] == []


def test_endpoint_project_mcp_does_not_attach_gitignored_deps(tmp_path):
    # P2 fix (Codex): _attach_mcp_launch_deps used include_gitignored=True
    # (endpoint-wide) even for project-scoped MCPs. A .claude/settings.json MCP
    # launching a local path in a gitignored dir must NOT attach that dir's deps.
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    project_root = tmp_path / "project"
    ignored_dir = project_root / "ignored"
    ignored_dir.mkdir(parents=True)
    (ignored_dir / "server.js").write_text("// server")
    (ignored_dir / "package.json").write_text(
        json.dumps({"name": "my-server", "dependencies": {"left-pad": "1.0.0"}})
    )
    (project_root / ".gitignore").write_text("ignored/\n")
    claude_dir = project_root / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps({"mcpServers": {"local": {"command": "node", "args": ["./ignored/server.js"]}}})
    )
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    g.validate()
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [c for c in g.children_of(mcp) if c.kind == "package"] == []


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


def test_malformed_plugin_manifest_does_not_suppress_sibling_mcp(tmp_path):
    # A plugin.json that parses but yields no self-ref (no `name`) must NOT
    # cause its directory to be treated as an owned plugin subtree, or it would
    # silently hide an otherwise-valid sibling `.mcp.json`.
    base = tmp_path / "packages" / "broken"
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text('{"version":"1"}')  # no name
    (base / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"git": {"command": "npx", "args": ["@org/git-mcp@1.0.0"]}}})
    )
    g = build_graph(tmp_path, mode="repo")  # must not raise
    # No plugin node was created for the broken manifest.
    assert not any(n.kind == "plugin" for n in g.nodes.values())
    # The sibling MCP server is still discovered, parented to the target.
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "target"]


def test_malformed_plugin_manifest_does_not_suppress_project_skill(tmp_path):
    base = tmp_path / "packages" / "broken"
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text("{not json")  # invalid JSON
    skill = base / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    g = build_graph(tmp_path, mode="repo")  # must not raise
    assert not any(n.kind == "plugin" for n in g.nodes.values())
    sk = next(n for n in g.nodes.values() if n.kind == "skill")
    assert g.lineage(sk)[-1].kind == "target"


def test_valid_plugin_still_owns_sibling_mcp(tmp_path):
    # Regression guard: a well-formed plugin still owns its subtree, so a
    # sibling `.mcp.json` parents to the plugin, not the target.
    base = tmp_path / "packages" / "good"
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text('{"name":"good","version":"1"}')
    (base / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"git": {"command": "npx", "args": ["@org/git-mcp@1.0.0"]}}})
    )
    g = build_graph(tmp_path, mode="repo")
    mcp = next(n for n in g.nodes.values() if n.kind == "mcp_server")
    assert [n.kind for n in g.lineage(mcp)] == ["mcp_server", "plugin", "target"]


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


def test_repo_subagent_precedence_matches_registry_accounting(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nh\n")
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    agent_nodes = [n for n in g.nodes.values() if n.kind == "agent"]
    assert len(agent_nodes) == 1
    assert agent_nodes[0].ref is not None
    assert agent_nodes[0].ref.extra["runtime_hosts"] == ["claude-code", "cursor"]


def test_repo_malformed_subagent_does_not_abort_graph_build(tmp_path, monkeypatch):
    # One malformed subagent .md must cost only that one node — the sibling
    # subagent must still show up as an agent node in the graph.
    from tools.parsers import claude_command_agent

    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "broken.md").write_text("---\nname: broken\n---\nx\n")
    (claude_agents / "healthy.md").write_text("---\nname: healthy\n---\ny\n")

    real_parse_file = claude_command_agent.parse_file

    def flaky_parse_file(path, *args, **kwargs):
        if path.name == "broken.md":
            raise ValueError("simulated parse failure")
        return real_parse_file(path, *args, **kwargs)

    monkeypatch.setattr(claude_command_agent, "parse_file", flaky_parse_file)

    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    agent_nodes = [n for n in g.nodes.values() if n.kind == "agent"]
    assert len(agent_nodes) == 1
    assert agent_nodes[0].ref is not None
    assert agent_nodes[0].ref.name == "healthy"


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


# --- Reproducible node keys: paths normalized relative to scan root ---


def test_repo_node_keys_contain_no_absolute_path(tmp_path):
    _skill_with_dep(tmp_path, ".claude/skills/deploy")
    g = build_graph(tmp_path, mode="repo")
    for n in g.nodes.values():
        if n.kind in ("package", "skill"):
            assert str(tmp_path) not in (n.key or ""), n.key


def test_repo_node_keys_reproducible_across_roots(tmp_path):
    def layout(root):
        root.mkdir()
        _skill_with_dep(root, ".claude/skills/deploy")
        (root / "package.json").write_text(
            '{"name":"app","version":"1.0.0","dependencies":{"left-pad":"1.0.0"}}'
        )
        return root

    root_a = layout(tmp_path / "a")
    root_b = layout(tmp_path / "b")
    g_a = build_graph(root_a, mode="repo")
    g_b = build_graph(root_b, mode="repo")
    keys_a = {n.key for n in g_a.nodes.values() if n.kind != "target"}
    keys_b = {n.key for n in g_b.nodes.values() if n.kind != "target"}
    assert keys_a == keys_b
    assert keys_a  # non-empty: actually exercised some nodes


def test_endpoint_node_keys_are_root_relative_and_labeled(tmp_path):
    install_root, project_root = _seed_endpoint_fixture(tmp_path)
    # project skill so a `project/` key exists too
    project_skill = project_root / ".claude" / "skills" / "ui"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text("---\nname: ui\ndescription: d\n---\nrun\n")
    g = build_graph(install_root, mode="endpoint", project_root=project_root)

    project_skill_node = next(
        n for n in g.nodes.values() if n.kind == "skill" and "ui" in (n.key or "")
    )
    assert (project_skill_node.key or "").startswith("project/"), project_skill_node.key

    # the plugin (under install_root) and its bundled nodes are endpoint-labeled
    plugin_node = next(n for n in g.nodes.values() if n.kind == "plugin")
    assert (plugin_node.key or "").startswith("endpoint/"), plugin_node.key

    for n in g.nodes.values():
        if n.kind == "target":
            continue
        assert str(install_root) not in (n.key or ""), n.key
        assert str(project_root) not in (n.key or ""), n.key


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
    assert component_nodes[0].ref.name == "helper-lib"
    assert component_nodes[0].ref.component_identity is None
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


def _bundled_skill(plugin_root, rel, *, skill_name, dep_name):
    d = plugin_root / rel
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {skill_name}\ndescription: d\n---\nrun\n")
    (d / "package.json").write_text(
        f'{{"name":"{skill_name}","version":"1","dependencies":{{"{dep_name}":"1.0.0"}}}}'
    )
    return d


def test_repo_bundled_skill_under_gitignored_path_excluded_by_default(tmp_path):
    """A plugin's bundled skill at a scan-root-gitignored path must NOT produce a
    skill node by default; a non-ignored sibling skill is still fully discovered
    (skill + its dep). With include_gitignored=True the ignored one appears too."""
    (tmp_path / ".gitignore").write_text("skills/private/\n")
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    _bundled_skill(tmp_path, "skills/public", skill_name="public-skill", dep_name="lodash")
    _bundled_skill(tmp_path, "skills/private", skill_name="private-skill", dep_name="left-pad")

    g = build_graph(tmp_path, mode="repo")
    skill_keys = [n.key for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_keys) == 1, "gitignored bundled skill must be excluded by default"
    pkg_keys = [n.key or "" for n in g.nodes.values() if n.kind == "package"]
    assert any("lodash" in k for k in pkg_keys), "non-ignored skill's dep must still be discovered"
    assert not any("left-pad" in k for k in pkg_keys), "gitignored skill's dep must not be reached"

    g_all = build_graph(tmp_path, mode="repo", include_gitignored=True)
    skill_keys_all = [n.key for n in g_all.nodes.values() if n.kind == "skill"]
    assert len(skill_keys_all) == 2, "include_gitignored=True must include the ignored skill"
    pkg_keys_all = [n.key or "" for n in g_all.nodes.values() if n.kind == "package"]
    assert any("left-pad" in k for k in pkg_keys_all), (
        "include_gitignored=True must reach the ignored skill's dep"
    )


def test_endpoint_enabled_plugin_missing_from_map_emits_warning(tmp_path):
    """An enabled plugin key absent from installed_plugins.json must append the
    parse_install-parity warning to the build_graph warnings channel."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    settings = {"enabledPlugins": {"ghost@mp": True}}
    (install_root / "settings.json").write_text(json.dumps(settings))
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    project_root.mkdir()

    warnings: list[str] = []
    build_graph(install_root, mode="endpoint", project_root=project_root, warnings=warnings)
    assert "plugin ghost@mp enabled but missing from installed_plugins.json" in warnings


def test_endpoint_ambiguous_install_entries_emit_scope_warning(tmp_path):
    """When installed_plugins.json has multiple valid entries and none matches
    the enabling scope, _select_install_entry returns a fallback warning that
    build_graph must surface (parity with parse_install)."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    install_path = install_root / "cache" / "demo" / "1.0.0"
    install_path.mkdir(parents=True)
    _skill_with_dep(install_path, "skills/deploy")
    # Enabled at user scope (settings.json is the user layer); both install
    # entries carry non-user scopes, so neither matches → fallback warning.
    (install_root / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@mp": True}}))
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@mp": [
                        {"scope": "project", "version": "1.0.0", "installPath": str(install_path)},
                        {"scope": "local", "version": "2.0.0", "installPath": str(install_path)},
                    ]
                },
            }
        )
    )
    project_root = tmp_path / "project"
    project_root.mkdir()

    warnings: list[str] = []
    build_graph(install_root, mode="endpoint", project_root=project_root, warnings=warnings)
    assert any("no scope match" in w for w in warnings), warnings


def test_repo_bundled_plugin_mcp_under_gitignore_excluded_by_default(tmp_path):
    """A plugin's bundled `.mcp.json` that the scan-root `.gitignore` excludes
    must not be emitted by default (parity with parse_repo_grouped's secondary-ref
    filtering); `--include-gitignored` restores it."""
    (tmp_path / ".gitignore").write_text(".mcp.json\n")
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"git":{"command":"npx","args":["@scope/git-mcp@1.2.3"]}}}'
    )
    g_default = build_graph(tmp_path, mode="repo")
    assert not [n for n in g_default.nodes.values() if n.kind == "mcp_server"]

    g_all = build_graph(tmp_path, mode="repo", include_gitignored=True)
    assert [n for n in g_all.nodes.values() if n.kind == "mcp_server"]


def test_endpoint_installed_plugin_gitignore_does_not_filter_bundled_mcp(tmp_path):
    """An active installed plugin whose install dir has a `.gitignore` ignoring
    `.mcp.json` must STILL emit its bundled MCP in endpoint mode. Installed
    plugins are artifacts, not repo source — the old `walk_plugin_root` path never
    filtered them by a `.gitignore`. Regression guard for the per-directory
    `_ignore_context` fallback that loaded the installed plugin's own `.gitignore`."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    install_path = install_root / "cache" / "demo" / "1.0.0"
    install_path.mkdir(parents=True)
    (install_path / ".gitignore").write_text(".mcp.json\n")
    (install_path / ".claude-plugin").mkdir()
    (install_path / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"demo","version":"1.0.0"}'
    )
    (install_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"git": {"command": "npx", "args": ["@scope/git-mcp@1.2.3"]}}})
    )
    (install_root / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@mp": True}}))
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
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert mcp_nodes, "endpoint must not gitignore-filter an installed plugin's bundled MCP"
    assert [n.kind for n in g.lineage(mcp_nodes[0])] == ["mcp_server", "plugin", "target"]


def test_endpoint_non_string_version_entry_emits_warning(tmp_path):
    """A plugin install entry with a non-string `version` is skipped; build_graph
    must surface the parse_install-parity warning rather than dropping the plugin
    (and all its components) silently."""
    install_root = tmp_path / "claude"
    install_root.mkdir()
    install_path = install_root / "cache" / "demo" / "1.0.0"
    install_path.mkdir(parents=True)
    _skill_with_dep(install_path, "skills/deploy")
    (install_root / "settings.json").write_text(json.dumps({"enabledPlugins": {"demo@mp": True}}))
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "demo@mp": [{"scope": "user", "version": 123, "installPath": str(install_path)}]
                },
            }
        )
    )
    project_root = tmp_path / "project"
    project_root.mkdir()

    warnings: list[str] = []
    build_graph(install_root, mode="endpoint", project_root=project_root, warnings=warnings)
    assert any("non-string version" in w for w in warnings), warnings


# --- Codex PR #131 review fixes ---


def test_endpoint_project_skill_source_provenance_stamped(tmp_path):
    """Project skills under <project>/.claude/skills must carry source_provenance
    when a skills-lock records their install source (parity with the old
    _walk_project_skill_dirs -> _parse_direct_skill path). Finding 1: the graph
    project-skill walk skipped this stamping; only direct endpoint skills had it.
    """
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    skill_dir = project_root / ".claude" / "skills" / "aws-api"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: aws-api\ndescription: d\n---\nrun\n")
    # skills-lock candidate: <project>/.claude/skills/../.skill-lock.json
    (project_root / ".claude" / "skills" / ".skill-lock.json").write_text(
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

    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref is not None
    provenance = skill_nodes[0].ref.extra.get("source_provenance")
    assert isinstance(provenance, dict), "project skill ref must carry source_provenance"
    assert provenance.get("source") == "https://github.com/user/aws-api-skill"
    assert provenance.get("ref") == "abc123"


def test_repo_dot_claude_skill_has_no_source_provenance(tmp_path):
    """Repo-mode .claude/skills must NOT stamp provenance: the old REGISTRY path
    called claude_skill.parse directly (no _parse_direct_skill), so repo skills
    never carried source_provenance even with a skills-lock present. Provenance
    stamping is scoped to the endpoint project-skill walk only."""
    skill_dir = tmp_path / ".claude" / "skills" / "aws-api"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: aws-api\ndescription: d\n---\nrun\n")
    (tmp_path / ".claude" / "skills" / ".skill-lock.json").write_text(
        json.dumps({"skills": {"aws-api": {"source": "https://example/x", "ref": "abc"}}})
    )
    g = build_graph(tmp_path, mode="repo")
    skill = next(n for n in g.nodes.values() if n.kind == "skill")
    assert skill.ref is not None
    assert skill.ref.extra.get("source_provenance") is None


def test_repo_manifest_and_lockfile_same_dir_emits_one_node_per_dep(tmp_path):
    """[correct-new-behavior] A dir with BOTH package.json and package-lock.json
    must emit the dependency ONCE (lockfile-preferred, ADR-0008), not twice.

    Before the fix _add_dep_manifest_packages iterated every present manifest, so
    a direct dep declared in package.json AND pinned in package-lock.json yielded
    two package nodes (occurrence keys differ by source_manifest, so no dedup) —
    the same vulnerable package reported twice. Now the lockfile is the sole npm
    source when present; the manifest is a fallback only when no lockfile exists.
    """
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"left-pad":"1.0.0"}}'
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "app", "version": "1.0.0"},
                    "node_modules/left-pad": {"version": "1.0.0"},
                    "node_modules/dep-transitive": {"version": "2.0.0"},
                },
            }
        )
    )
    g = build_graph(tmp_path, mode="repo")
    pkgs = [n for n in g.nodes.values() if n.kind == "package"]
    # left-pad (direct) + dep-transitive (transitive), both from the lockfile;
    # the package.json left-pad is suppressed.
    assert len(pkgs) == 2
    left_pad = [n for n in pkgs if "left-pad" in (n.key or "")]
    assert len(left_pad) == 1
    assert left_pad[0].ref is not None
    assert (left_pad[0].ref.extra or {}).get("transitive") is True
    assert "package-lock.json" in (left_pad[0].ref.source_manifest or "")


def test_repo_manifest_only_falls_back_when_no_lockfile(tmp_path):
    """No lockfile present -> the manifest is the fallback source (parity with
    _walk_plugin_implementation_deps' _MANIFEST_FALLBACK)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1.0.0"\ndependencies = ["requests==2.0.0"]\n'
    )
    g = build_graph(tmp_path, mode="repo")
    pkgs = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkgs) == 1
    assert pkgs[0].ref is not None
    assert "pyproject.toml" in (pkgs[0].ref.source_manifest or "")


def test_repo_npm_and_pypi_lockfiles_coexist(tmp_path):
    """Multi-ecosystem dir: npm lockfile + PyPI manifest (no uv.lock) both emit;
    lockfile-preferred is per-ecosystem, not global."""
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1","dependencies":{"left-pad":"1.0.0"}}'
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "app", "version": "1"},
                    "node_modules/left-pad": {"version": "1.0.0"},
                },
            }
        )
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1"\ndependencies = ["requests==2.0.0"]\n'
    )
    g = build_graph(tmp_path, mode="repo")
    srcs = {
        Path(n.ref.source_manifest).name
        for n in g.nodes.values()
        if n.kind == "package" and n.ref is not None and n.ref.source_manifest
    }
    assert "package-lock.json" in srcs  # npm from lockfile
    assert "pyproject.toml" in srcs  # PyPI from manifest fallback
    assert "package.json" not in srcs  # npm manifest suppressed


def _endpoint_with_symlinked_project_skill(root):
    """Endpoint layout whose project skill is a SYMLINK pointing OUTSIDE the
    project (a real-world setup: a shared skills repo linked into a project)."""
    install_root = root / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )

    project_root = root / "project"
    (project_root / ".claude" / "skills").mkdir(parents=True)

    # The real skill dir lives outside project_root; the project links to it.
    external = root / "external-skills" / "deploy"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    (external / "package.json").write_text(
        '{"name":"deploy","version":"1","dependencies":{"lodash":"4.17.20"}}'
    )
    (project_root / ".claude" / "skills" / "deploy").symlink_to(external, target_is_directory=True)

    return install_root, project_root


def test_endpoint_symlinked_project_skill_key_uses_logical_project_path(tmp_path):
    # [bug-fixed] A project skill that is a symlink pointing outside project_root
    # carries a LOGICAL source_manifest under project_root. The normalizer used to
    # .resolve() it first, following the link out of the root, so relative_to()
    # failed and the node key fell back to the machine-specific absolute path.
    # Relativizing the logical path keeps the stable `project/` label.
    install_root, project_root = _endpoint_with_symlinked_project_skill(tmp_path)
    g = build_graph(install_root, mode="endpoint", project_root=project_root)

    skill = next(n for n in g.nodes.values() if n.kind == "skill")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert skill.key.startswith("project/.claude/skills/deploy/"), skill.key
    assert pkg.key.startswith("project/.claude/skills/deploy/"), pkg.key
    # no absolute machine path leaked into the keys
    assert str(tmp_path) not in skill.key
    assert str(tmp_path) not in pkg.key


def test_endpoint_symlinked_project_skill_key_reproducible_across_roots(tmp_path):
    # The non-target keys must be identical when the SAME layout is built under two
    # different temp roots — the property the logical relativization guarantees.
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    install_a, project_a = _endpoint_with_symlinked_project_skill(root_a)
    install_b, project_b = _endpoint_with_symlinked_project_skill(root_b)

    g_a = build_graph(install_a, mode="endpoint", project_root=project_a)
    g_b = build_graph(install_b, mode="endpoint", project_root=project_b)

    keys_a = sorted(k for k, n in g_a.nodes.items() if n.kind != "target")
    keys_b = sorted(k for k, n in g_b.nodes.items() if n.kind != "target")
    assert keys_a == keys_b
    assert keys_a  # non-empty: the symlinked skill + its dep were discovered


# --- Skill lock source provenance propagated through graph ---


def _skills_lock_entry(source: str = "vercel-labs/agent-skills") -> str:
    return json.dumps(
        {
            "version": 1,
            "skills": {
                "bootstrap": {
                    "source": source,
                    "sourceType": "github",
                    "ref": "main",
                    "skillPath": "skills/bootstrap/SKILL.md",
                    "computedHash": "abcdef1234567890",
                }
            },
        }
    )


def test_endpoint_project_skill_carries_skills_lock_provenance(tmp_path):
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    skill_dir = project_root / ".claude" / "skills" / "bootstrap"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: bootstrap\ndescription: d\n---\nrun\n")
    (project_root / "skills-lock.json").write_text(_skills_lock_entry())

    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    skill = next(n for n in g.nodes.values() if n.kind == "skill")
    assert skill.ref is not None
    provenance = (skill.ref.extra or {}).get("source_provenance")
    assert provenance is not None, "project skill should carry skills-lock provenance"
    assert provenance["source"] == "vercel-labs/agent-skills"
    assert provenance["source_type"] == "github"


def test_plugin_bundled_skill_does_not_look_up_skills_lock(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    skill_dir = tmp_path / "skills" / "bootstrap"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: bootstrap\ndescription: d\n---\nrun\n")
    # Place a skills-lock.json at the repo root — must NOT be picked up for
    # plugin-bundled skills (those are not project skills and have no lockfile).
    (tmp_path / "skills-lock.json").write_text(_skills_lock_entry())

    g = build_graph(tmp_path, mode="repo")
    skill = next(n for n in g.nodes.values() if n.kind == "skill")
    assert skill.ref is not None
    assert (skill.ref.extra or {}).get("source_provenance") is None


def test_manifest_name_index_skips_node_modules(tmp_path):
    # Finding 1: in endpoint mode include_gitignored=True causes the walk to
    # descend into node_modules/. The index must never return those entries so
    # an external `npx <pkg>` cannot resolve to an installed copy and be
    # mis-attributed as a local self-launch.
    node_mod = tmp_path / "node_modules" / "some-dep"
    node_mod.mkdir(parents=True)
    (node_mod / "package.json").write_text('{"name": "some-dep"}')
    # Also add a legitimate first-party package to confirm it IS indexed.
    (tmp_path / "package.json").write_text('{"name": "my-app"}')
    idx = build_manifest_name_index(tmp_path, include_gitignored=True)
    assert ("npm", "some-dep") not in idx, "node_modules entry must be excluded from name index"
    assert ("npm", "my-app") in idx, "first-party root manifest must still be indexed"


def test_repo_cursor_mcp_json_becomes_direct_child(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["cursor"])
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref is not None
    assert mcp_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]


def test_repo_cursor_mcp_json_absent_when_host_not_selected(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert mcp_nodes == []


def test_repo_default_hosts_omitted_is_every_registered_host(tmp_path):
    # build_graph's default matches parse_repo_grouped's (Task 6) — both
    # "every registered host" — so a caller that uses one without the
    # other still sees the same inventory. Existing tests in this file
    # are unaffected: none of their fixtures create a .cursor/ directory,
    # so this broadening finds nothing extra for any of them.
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo")  # hosts omitted
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref is not None
    assert mcp_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]


def test_repo_cursor_cache_mcp_json_is_claude_owned(tmp_path):
    # Same boundary case as Task 6's registry-level test, at the graph
    # layer: .cursor/cache/mcp.json is nested under .cursor/ but isn't
    # the exact .cursor/mcp.json shape — must be found and tagged
    # claude-code, not silently dropped by both branches.
    nested = tmp_path / ".cursor" / "cache"
    nested.mkdir(parents=True)
    (nested / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref is not None
    assert mcp_nodes[0].ref.extra["runtime_hosts"] == ["claude-code"]


def test_repo_cursor_commands_are_graph_discoverable(tmp_path):
    cursor_commands = tmp_path / ".cursor" / "commands"
    cursor_commands.mkdir(parents=True)
    (cursor_commands / "deploy.md").write_text("run\n")
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    command_nodes = [n for n in g.nodes.values() if n.kind == "command"]
    assert len(command_nodes) == 1
    assert command_nodes[0].ref is not None
    assert command_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]


def test_repo_claude_commands_unaffected_by_cursor_dispatch_change(tmp_path):
    claude_commands = tmp_path / ".claude" / "commands"
    claude_commands.mkdir(parents=True)
    (claude_commands / "deploy.md").write_text("run\n")
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    command_nodes = [n for n in g.nodes.values() if n.kind == "command"]
    assert len(command_nodes) == 1
    assert command_nodes[0].ref is not None
    assert "runtime_hosts" not in command_nodes[0].ref.extra


def test_build_graph_rejects_colliding_host_patterns(tmp_path, monkeypatch):
    from tools.hosts import HOSTS, HostAdapter

    def _collider_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="collider",
                extra={"component_type": "mcp_server", "runtime_hosts": ["collider-host"]},
            )
        ]

    collider_adapter = HostAdapter(
        host_id="collider-host",
        detect=lambda: False,
        config_root=lambda override: None,
        manifest_registry=[("mcp.json", _collider_parse)],
        posture_rule_ids=frozenset(),
    )
    monkeypatch.setitem(HOSTS, "collider-host", collider_adapter)

    (tmp_path / "mcp.json").write_text("{}")
    with pytest.raises(ValueError, match="mcp.json"):
        build_graph(tmp_path, mode="repo", hosts=["claude-code", "collider-host"])


def test_build_graph_rejects_unknown_host(tmp_path):
    with pytest.raises(ValueError, match="typo"):
        build_graph(tmp_path, mode="repo", hosts=["typo"])


def test_repo_cursor_skills_dir_found(tmp_path):
    skill_dir = tmp_path / ".cursor" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nbody\n")
    g = build_graph(tmp_path, mode="repo", hosts=["cursor"])
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref is not None
    assert skill_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]


def test_repo_agents_skills_dir_tagged_cursor_only(tmp_path):
    # Not ["cursor", "codex"]: Codex isn't a registered host in this plan
    # (no HOSTS["codex"] entry), so the scan never actually verified it.
    skill_dir = tmp_path / ".agents" / "skills" / "shared"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: shared\ndescription: d\n---\nbody\n")
    g = build_graph(tmp_path, mode="repo", hosts=["cursor"])
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref is not None
    assert skill_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]


def test_repo_cursor_skills_absent_when_host_not_selected(tmp_path):
    skill_dir = tmp_path / ".cursor" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nbody\n")
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    assert [n for n in g.nodes.values() if n.kind == "skill"] == []


def test_repo_claude_skills_now_tagged_claude_code(tmp_path):
    # Regression/behavior-change guard from Task 4: existing Claude skill
    # refs now carry runtime_hosts, threaded correctly through build_graph.
    skill_dir = tmp_path / ".claude" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nbody\n")
    g = build_graph(tmp_path, mode="repo")  # hosts omitted -> every registered host,
    # but this fixture has no .cursor/ content, so only the Claude skill is found
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert skill_nodes[0].ref is not None
    assert skill_nodes[0].ref.extra["runtime_hosts"] == ["claude-code"]


def test_endpoint_project_skills_ignore_cursor_dir(tmp_path):
    # Regression guard: endpoint mode is Claude-only (this plan's Goal line,
    # "without touching endpoint mode") — a project's .cursor/skills must not
    # surface as a skill node even though repo mode now discovers it by
    # default. _seed_endpoint's _add_project_skills call must stay pinned to
    # hosts=["claude-code"], not inherit all_host_ids().
    install_root = tmp_path / "claude"
    install_root.mkdir()
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    project_root = tmp_path / "project"
    claude_skill_dir = project_root / ".claude" / "skills" / "legacy"
    claude_skill_dir.mkdir(parents=True)
    (claude_skill_dir / "SKILL.md").write_text("---\nname: legacy\ndescription: d\n---\nbody\n")
    cursor_skill_dir = project_root / ".cursor" / "skills" / "deploy"
    cursor_skill_dir.mkdir(parents=True)
    (cursor_skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nbody\n")
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref is not None
    assert skill_nodes[0].ref.name == "legacy"
    assert skill_nodes[0].ref.extra["runtime_hosts"] == ["claude-code"]
    assert all(
        n.ref is not None and n.ref.extra.get("runtime_hosts") != ["cursor"] for n in skill_nodes
    )


def test_synthetic_host_registered_in_hosts_is_graph_discoverable(tmp_path, monkeypatch):
    # Registering a HostAdapter must be sufficient for build_graph() — the
    # path tools/scan.py calls "the single source of truth" for scope,
    # attribution, BOM, and findings — to pick up a new host's components.
    # Both registry-driven surfaces (MCP and Skills) are asserted.
    from tools.hosts import HOSTS, HostAdapter

    def _synthetic_mcp_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="synthetic-widget",
                extra={"component_type": "mcp_server", "runtime_hosts": ["synthetic-host"]},
            )
        ]

    def _synthetic_skill_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="synthetic-skill",
                extra={"component_type": "skill", "runtime_hosts": ["synthetic-host"]},
            )
        ]

    synthetic_adapter = HostAdapter(
        host_id="synthetic-host",
        detect=lambda: False,
        config_root=lambda override: None,
        # Reuses two already-allowlisted pattern shapes rather than inventing
        # new ones. Run with hosts=["synthetic-host"] alone, so reusing
        # Claude's bare "mcp.json" pattern here doesn't collide with Claude's
        # own registration.
        manifest_registry=[
            ("mcp.json", _synthetic_mcp_parse),
            ("**/.agents/skills/*/SKILL.md", _synthetic_skill_parse),
        ],
        posture_rule_ids=frozenset(),
    )
    monkeypatch.setitem(HOSTS, "synthetic-host", synthetic_adapter)

    (tmp_path / "mcp.json").write_text("{}")
    skill_dir = tmp_path / ".agents" / "skills" / "synthetic-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: synthetic-skill\n---\nbody\n")

    g = build_graph(tmp_path, mode="repo", hosts=["synthetic-host"])
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref is not None
    assert mcp_nodes[0].ref.name == "synthetic-widget"
    assert len(skill_nodes) == 1
    assert skill_nodes[0].ref is not None
    assert skill_nodes[0].ref.name == "synthetic-skill"


# Cursor Plugins (native format): registry entry + graph realization. See
# docs/specs/multi-host-support.md Plugins section.


def test_repo_cursor_plugin_graph_discoverable(tmp_path):
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin"]
    assert len(plugin_nodes) == 1
    assert plugin_nodes[0].ref is not None
    assert plugin_nodes[0].ref.extra["runtime_hosts"] == ["cursor"]


def test_repo_cursor_plugin_absent_from_graph_when_cursor_not_selected(tmp_path):
    # The host gate every sibling surface has: `.cursor-plugin/plugin.json` is
    # Cursor's registry entry, so a Claude-only scan must produce no plugin
    # node — and no bundled surface leaking in through another branch either.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    (plugin_root / "skills" / "helper").mkdir(parents=True)
    (plugin_root / "skills" / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: d\n---\nrun\n"
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    assert not [n for n in g.nodes.values() if n.kind != "target"]


def test_repo_cursor_native_plugin_bundled_mcp_excluded_when_cursor_not_selected(tmp_path):
    # Regression guard: Cursor's default bundled MCP filename is the bare
    # "mcp.json" — the same basename Claude Code's own bare-mcp.json registry
    # pattern matches. An unselected-host native plugin bundle's root must
    # still be excluded from the standalone-surface walk (a bundle boundary,
    # even though no plugin node is realized for it), or `<bundle>/mcp.json`
    # falls through and gets attributed as a Claude Code component directly
    # under the target instead of being dropped for the unselected host.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    assert not [n for n in g.nodes.values() if n.kind != "target"]


def test_repo_build_graph_reports_unselected_native_plugin_root_via_excluded_plugin_roots(
    tmp_path,
):
    # `excluded_plugin_roots` is the side channel callers doing an independent
    # filesystem walk of the same directory (posture manifest collection in
    # tools/scan.py) use to learn the same bundle boundary the graph enforces
    # internally, for a bundle whose owning host wasn't selected.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    excluded_plugin_roots: list[Path] = []
    build_graph(
        tmp_path,
        mode="repo",
        hosts=["claude-code"],
        excluded_plugin_roots=excluded_plugin_roots,
    )
    assert [p.resolve() for p in excluded_plugin_roots] == [plugin_root.resolve()]


def test_repo_build_graph_omits_realized_plugin_root_from_excluded_plugin_roots(tmp_path):
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".cursor-plugin").mkdir(parents=True)
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    excluded_plugin_roots: list[Path] = []
    build_graph(
        tmp_path,
        mode="repo",
        hosts=["claude-code", "cursor"],
        excluded_plugin_roots=excluded_plugin_roots,
    )
    assert excluded_plugin_roots == []


def test_repo_dual_native_plugin_manifests_resolve_to_claude_format(tmp_path):
    # One directory carrying BOTH native manifests resolves to exactly one
    # plugin root, and the winner is the `.claude-plugin` one — the walk
    # visits `.claude-plugin/` before `.cursor-plugin/`, so precedence is
    # walk order, NOT host-selection order. Pinned in both host orders so a
    # future switch to first-selected-host precedence fails loudly here.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text('{"name": "claude-format"}')
    (plugin_root / ".cursor-plugin").mkdir()
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "cursor-format"}')
    for hosts in (["claude-code", "cursor"], ["cursor", "claude-code"]):
        g = build_graph(tmp_path, mode="repo", hosts=hosts)
        plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin"]
        assert len(plugin_nodes) == 1, hosts
        assert plugin_nodes[0].ref is not None
        assert plugin_nodes[0].ref.name == "claude-format", hosts


def test_repo_dual_native_plugin_manifests_falls_back_when_preferred_is_malformed(tmp_path):
    # When a directory carries BOTH native manifests but the preferred
    # (.claude-plugin) one is malformed, the root must not be dropped
    # entirely — the valid .cursor-plugin candidate realizes instead, and
    # the bundle's root mcp.json stays owned by the plugin rather than
    # falling through to standalone discovery and being misattributed.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text("{not json")
    (plugin_root / ".cursor-plugin").mkdir()
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "cursor-format"}')
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin"]
    assert len(plugin_nodes) == 1
    assert plugin_nodes[0].ref is not None
    assert plugin_nodes[0].ref.name == "cursor-format"
    parent_of = {e.child: e.parent for e in g.edges}
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert parent_of[mcp_nodes[0].key] == plugin_nodes[0].key


def test_repo_dual_plugin_manifests_excludes_bundle_selected_malformed_sibling_unselected(
    tmp_path,
):
    # `--host claude-code` only: the selected-host (.claude-plugin) manifest
    # is malformed, and the only other candidate (.cursor-plugin) belongs to
    # an unselected host. Neither realizes a plugin node, but the directory
    # is still a real plugin bundle (for Cursor) — its root mcp.json must NOT
    # fall through to standalone discovery and get misattributed to Claude
    # Code, the same as a bundle with only an unselected-host candidate.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text("{not json")
    (plugin_root / ".cursor-plugin").mkdir()
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "cursor-format"}')
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    assert not [n for n in g.nodes.values() if n.kind != "target"]
    excluded_plugin_roots: list[Path] = []
    build_graph(
        tmp_path,
        mode="repo",
        hosts=["claude-code"],
        excluded_plugin_roots=excluded_plugin_roots,
    )
    assert [p.resolve() for p in excluded_plugin_roots] == [plugin_root.resolve()]


def test_repo_build_graph_reports_realized_plugin_manifest_that_won(tmp_path):
    # `realized_plugin_manifests` records exactly which candidate manifest
    # `_descend_into_plugin` actually parsed, so callers doing an independent
    # filesystem walk (posture collection in tools/scan.py) can tell a
    # winning manifest apart from a losing sibling in the same bundle root.
    plugin_root = tmp_path / "my-plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text('{"name": "claude-format"}')
    (plugin_root / ".cursor-plugin").mkdir()
    (plugin_root / ".cursor-plugin" / "plugin.json").write_text('{"name": "cursor-format"}')
    realized_plugin_manifests: dict[Path, Path] = {}
    build_graph(
        tmp_path,
        mode="repo",
        hosts=["claude-code", "cursor"],
        realized_plugin_manifests=realized_plugin_manifests,
    )
    assert {p.resolve(): m.resolve() for p, m in realized_plugin_manifests.items()} == {
        plugin_root.resolve(): (plugin_root / ".claude-plugin" / "plugin.json").resolve()
    }


def test_repo_cursor_plugin_bundled_components_nest_under_plugin_node(tmp_path):
    # The graph-realization contract: bundled components are children of the
    # plugin node, never direct children of target, and every bundled ref
    # carries Cursor provenance sourced from inside the plugin bundle. A
    # node-presence check can't detect flattening or dropped host tags;
    # assert the actual edges, runtime_hosts, and source manifests. The
    # plugin lives in a subdirectory so bundle-relative sourcing is a real
    # assertion, not trivially true of the whole scan root.
    plugin_root = tmp_path / "my-plugin"
    plugin_dir = plugin_root / ".cursor-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        '{"name": "demo", "hooks": {"postToolUse": [{"command": "echo done"}]}}'
    )
    skills_dir = plugin_root / "skills" / "helper"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: helper\ndescription: d\n---\nrun\n")
    (plugin_root / "commands").mkdir()
    (plugin_root / "commands" / "deploy.md").write_text("run\n")
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_node = next(n for n in g.nodes.values() if n.kind == "plugin")
    parent_of = {e.child: e.parent for e in g.edges}
    # "mcp_server" only exists if the realization used Cursor's root
    # `mcp.json` default, and "hook" only exists if the inline-hooks block in
    # `.cursor-plugin/plugin.json` was read — both fail against a
    # Claude-hardcoded reread. The hook's exact source path is pinned by the
    # Step 3 parser test (a fabricated `.claude-plugin` path would still sit
    # inside the bundle, so the relative check below can't distinguish it).
    for kind in ("skill", "command", "mcp_server", "hook"):
        nodes = [n for n in g.nodes.values() if n.kind == kind]
        assert nodes, f"no {kind} node found"
        for n in nodes:
            assert parent_of[n.key] == plugin_node.key, (
                f"{kind} node attached to {parent_of[n.key]}, not the plugin"
            )
            assert n.ref is not None
            assert n.ref.extra["runtime_hosts"] == ["cursor"], (
                f"{kind} bundled ref lost Cursor provenance"
            )
            assert Path(n.ref.source_manifest).resolve().is_relative_to(plugin_root.resolve()), (
                f"{kind} bundled ref sourced outside the plugin bundle"
            )


# Agent Plugins (open standard): closed, parser-output-only realization. See
# docs/specs/multi-host-support.md Plugins section and ADR-0045 Decision #3.


def test_agent_plugin_graph_nests_portable_surfaces_only(tmp_path):
    # Skills and MCP nest under the plugin node; every deliberately
    # unsupported surface present in the bundle — commands, agents, hooks
    # (inline and hooks/hooks.json), manifest dependencies, extensions —
    # produces NO node at all. The plugin lives in a subdirectory so the
    # assertions can't be satisfied by target-level accidents.
    plugin_root = tmp_path / "my-plugin"
    plugin_root.mkdir()
    (plugin_root / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
                "hooks": {"postToolUse": [{"command": "echo done"}]},
                "dependencies": ["left-pad@1.0.0"],
                "extensions": {"com.cursor": {"rules": ["r1"]}},
            }
        )
    )
    skills_dir = plugin_root / "skills" / "helper"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: helper\ndescription: d\n---\nrun\n")
    (skills_dir / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}')
    (plugin_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    (plugin_root / "commands").mkdir()
    (plugin_root / "commands" / "deploy.md").write_text("run\n")
    (plugin_root / "agents").mkdir()
    (plugin_root / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nreview\n")
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text('{"hooks": {"postToolUse": [{"command": "./check.sh"}]}}')
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_node = next(n for n in g.nodes.values() if n.kind == "plugin")
    parent_of = {e.child: e.parent for e in g.edges}  # adapt per tools/graph.py, as in Task 13
    for kind in ("command", "agent", "hook"):
        assert not [n for n in g.nodes.values() if n.kind == kind], kind
    plugin_children = [n for n in g.nodes.values() if parent_of.get(n.key) == plugin_node.key]
    assert {n.kind for n in plugin_children} == {"skill", "mcp_server"}
    for n in plugin_children:
        assert n.ref is not None
        assert n.ref.extra["runtime_hosts"] == ["cursor"]
    # The bundled skill keeps its normal dep chain (closed realization
    # suppresses the plugin bundle walk, not skill-level analysis).
    skill_node = next(n for n in plugin_children if n.kind == "skill")
    skill_children = [n for n in g.nodes.values() if parent_of.get(n.key) == skill_node.key]
    assert any(n.kind == "package" for n in skill_children)


def test_agent_plugin_absent_from_graph_when_cursor_not_selected(tmp_path):
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "demo"}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code"])
    assert not [n for n in g.nodes.values() if n.kind == "plugin"]


def test_malformed_agent_plugin_does_not_swallow_sibling_mcp_json(tmp_path):
    # A schema-tagged plugin.json missing `name` is detected but yields no
    # plugin self ref, so `_realize_agent_plugin` attaches nothing — its
    # sibling mcp.json is a real, independent standalone MCP surface and
    # must still be discovered, not silently claimed as "already handled."
    (tmp_path / "plugin.json").write_text(
        json.dumps({"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"})
    )
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    assert not [n for n in g.nodes.values() if n.kind == "plugin"]
    mcp_nodes = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcp_nodes) == 1
    assert mcp_nodes[0].ref is not None
    assert mcp_nodes[0].ref.name == "weather-mcp"


def test_agent_plugin_bundled_skill_dep_honors_scan_root_gitignore(tmp_path):
    # Parity with Task 13's native descent (_descend_into_plugin) and with
    # test_repo_root_gitignore_skips_nested_skill_dep_manifest: a scan-root
    # .gitignore rule must reach into an Agent Plugin's bundled skill dir too.
    plugin_root = tmp_path / "my-plugin"
    plugin_root.mkdir()
    (plugin_root / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    skills_dir = plugin_root / "skills" / "helper"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: helper\ndescription: d\n---\nrun\n")
    (skills_dir / "package.json").write_text('{"dependencies": {"left-pad": "1.0.0"}}')
    (tmp_path / ".gitignore").write_text("my-plugin/skills/helper/package.json\n")

    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    pkg_nodes = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkg_nodes) == 0, "scan-root gitignore must skip the bundled skill's package.json"
    assert len([n for n in g.nodes.values() if n.kind == "skill"]) == 1

    # include_gitignored=True bypasses filtering: the dep reappears.
    g2 = build_graph(
        tmp_path, mode="repo", include_gitignored=True, hosts=["claude-code", "cursor"]
    )
    assert len([n for n in g2.nodes.values() if n.kind == "package"]) == 1


def test_target_level_package_json_beside_root_agent_plugin_keeps_dep_nodes(tmp_path):
    # A root-level Agent Plugins plugin.json does NOT join the native
    # branch's realized_roots exclusion, so a sibling target-level
    # package.json must keep its target-level dep nodes (Claude-only
    # backward-compatibility guarantee the closed contract must not break).
    (tmp_path / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}'
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin"]
    assert len(plugin_nodes) == 1
    parent_of = {e.child: e.parent for e in g.edges}
    pkg_nodes = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkg_nodes) == 1
    assert pkg_nodes[0].ref is not None
    assert pkg_nodes[0].ref.name == "lodash"
    # Attached under the target, not swallowed by (or reparented under) the
    # Agent Plugin — the closed realization never reads the plugin root's own
    # dependency manifests.
    assert parent_of[pkg_nodes[0].key] == g.root.key


def test_agent_plugin_host_private_subtree_excluded_from_standalone_discovery(tmp_path):
    # The closed Agent Plugins contract (`_realize_agent_plugin`) only ever
    # attaches self/skills/mcp — but the bundle's whole subtree must still be
    # excluded from the LATER standalone-surface and subagent passes below,
    # not just the realized nodes. A host-private `.cursor/agents/x.md`
    # nested in the bundle root would otherwise be picked up as a top-level
    # target subagent, double-scoping the same file to two surfaces.
    plugin_root = tmp_path / "my-plugin"
    plugin_root.mkdir()
    (plugin_root / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    agents_dir = plugin_root / ".cursor" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.md").write_text("---\nname: reviewer\n---\nreview\n")

    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    assert not [n for n in g.nodes.values() if n.kind == "agent"]


@pytest.mark.parametrize("config_dir", [".claude", ".agents", ".cursor"])
def test_agent_plugin_manifest_in_host_config_dir_is_not_a_bundle_root(tmp_path, config_dir):
    # A schema-tagged plugin.json dropped into a HOST-owned config dir whose
    # `skills/` is independently discovered by the registry walk would make
    # `_realize_agent_plugin` re-parent the very skill `_add_project_skills`
    # already attached to the target — the same occurrence under two parents,
    # which aborts the whole scan with GraphInvariantError. Such a directory
    # is host config, not an Agent Plugins bundle root: the registry-driven
    # discovery keeps the skill and no plugin is realized.
    cfg = tmp_path / config_dir
    (cfg / "skills" / "helper").mkdir(parents=True)
    (cfg / "skills" / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: d\n---\nrun\n"
    )
    (cfg / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    assert not [n for n in g.nodes.values() if n.kind == "plugin"]
    skill_nodes = [n for n in g.nodes.values() if n.kind == "skill"]
    assert len(skill_nodes) == 1
    parent_of = {e.child: e.parent for e in g.edges}
    assert parent_of[skill_nodes[0].key] == g.root.key


def _minimal_claude_install_root(tmp_path: Path) -> Path:
    install_root = tmp_path / "claude"
    (install_root / "plugins").mkdir(parents=True)
    (install_root / "settings.json").write_text("{}")
    (install_root / "plugins" / "installed_plugins.json").write_text(
        '{"version": 1, "plugins": {}}'
    )
    (install_root / "skills" / "helper").mkdir(parents=True)
    (install_root / "skills" / "helper" / "SKILL.md").write_text(
        "---\nname: helper\ndescription: d\n---\nrun\n"
    )
    (install_root / "agents").mkdir()
    (install_root / "agents" / "reviewer.md").write_text("---\nname: reviewer\n---\nr\n")
    (install_root / "commands").mkdir()
    (install_root / "commands" / "deploy.md").write_text("run\n")
    return install_root


def test_endpoint_claude_only_graph_snapshot_unchanged(tmp_path):
    """Claude-only endpoint output is frozen: this list was captured against
    the pre-multi-host `_seed_endpoint` and must never change."""
    install_root = _minimal_claude_install_root(tmp_path)
    g = build_graph(install_root, mode="endpoint")
    snapshot = sorted((n.kind, n.key) for n in g.nodes.values())
    assert snapshot == [
        ("agent", "endpoint/agents/reviewer.md#$#reviewer"),
        ("command", "endpoint/commands/deploy.md#$#deploy"),
        ("skill", "endpoint/skills/helper/SKILL.md#$.frontmatter#helper"),
        ("target", "openaca:target"),
    ]
    assert not any(key.startswith("endpoint-") for _, key in snapshot)
    agents = [n for n in g.nodes.values() if n.kind == "agent"]
    assert len(agents) == 1
    assert agents[0].ref is not None
    assert agents[0].ref.extra == {
        "scope_owner": None,
        "component_type": "agent",
        "_identity_finalized": True,
    }


def test_endpoint_normalizer_single_root_unchanged(tmp_path):
    from tools.graph_build import _make_normalizer

    claude_root = tmp_path / "claude"
    normalize = _make_normalizer(
        "endpoint", claude_root, None, discovery_roots={"endpoint": claude_root}
    )
    assert normalize(str(claude_root / "mcp.json")) == "endpoint/mcp.json"
    # Omitting the descriptor entirely keeps the historical single-root form.
    legacy = _make_normalizer("endpoint", claude_root, None)
    assert legacy(str(claude_root / "mcp.json")) == "endpoint/mcp.json"


def test_endpoint_normalizer_two_roots_source_manifest_to_prefix_mapping(tmp_path):
    # Each source manifest maps to its OWNING root's prefix; the same relative
    # path under both roots yields two distinct keys.
    from tools.endpoint_request import endpoint_discovery_roots
    from tools.graph_build import _make_normalizer

    claude_root, cursor_root = tmp_path / "claude", tmp_path / "cursor"
    discovery_roots = endpoint_discovery_roots(
        ["claude-code", "cursor"],
        {"claude-code": claude_root, "cursor": cursor_root},
    )
    normalize = _make_normalizer("endpoint", claude_root, None, discovery_roots=discovery_roots)
    assert normalize(str(claude_root / "mcp.json")) == "endpoint/mcp.json"
    assert normalize(str(cursor_root / "mcp.json")) == "endpoint-cursor/mcp.json"
    outside = tmp_path / "elsewhere" / "x.json"
    assert normalize(str(outside)) == str(outside)


def test_endpoint_normalizer_labels_cursor_auxiliary_roots(tmp_path, monkeypatch):
    # Cursor's two home-scoped auxiliary roots are unrelated to both the
    # explicit config override and the project root, yet still receive stable,
    # distinct labels — neither may fall through to a machine-absolute key.
    from tools.endpoint_request import endpoint_discovery_roots
    from tools.graph_build import _make_normalizer

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "claude-compat"))
    cursor_root = tmp_path / "overrides" / "cursor-config"
    discovery_roots = endpoint_discovery_roots(["cursor"], {"cursor": cursor_root})
    normalize = _make_normalizer("endpoint", cursor_root, None, discovery_roots=discovery_roots)
    shared = home / ".agents" / "skills" / "helper" / "SKILL.md"
    compat = home / "claude-compat" / "agents" / "helper.md"
    assert normalize(str(shared)) == "endpoint-shared-agents/skills/helper/SKILL.md"
    assert normalize(str(compat)) == "endpoint-claude-compat/agents/helper.md"
    assert not Path(normalize(str(shared))).is_absolute()
    assert not Path(normalize(str(compat))).is_absolute()


def test_endpoint_discovery_roots_rejects_two_hosts_on_one_directory(tmp_path):
    import click

    from tools.endpoint_request import endpoint_discovery_roots

    shared = tmp_path / "one-config"
    shared.mkdir()
    with pytest.raises(click.ClickException):
        endpoint_discovery_roots(
            ["claude-code", "cursor"], {"claude-code": shared, "cursor": shared}
        )


def test_endpoint_launch_dep_binds_owning_root_not_other_host(tmp_path):
    # Contract item 4's no-cross-host rule: BOTH roots hold a package dir named
    # "server" declaring a different dependency, and each root's MCP launches
    # "server" by name. Each MCP must attach only its OWN root's dependency.
    claude_root = _minimal_claude_install_root(tmp_path)
    (claude_root / "server").mkdir()
    (claude_root / "server" / "package.json").write_text(
        '{"name": "server", "version": "1.0.0", "dependencies": {"left-pad": "1.0.0"}}'
    )
    (claude_root / ".mcp.json").write_text(
        '{"mcpServers": {"claude-mcp": {"command": "npx", "args": ["server"]}}}'
    )
    cursor_root = tmp_path / "cursor"
    (cursor_root / "server").mkdir(parents=True)
    (cursor_root / "server" / "package.json").write_text(
        '{"name": "server", "version": "1.0.0", "dependencies": {"right-pad": "2.0.0"}}'
    )
    (cursor_root / "mcp.json").write_text(
        '{"mcpServers": {"cursor-mcp": {"command": "npx", "args": ["server"]}}}'
    )

    g = build_graph(
        claude_root,
        mode="endpoint",
        host_config_roots={"claude-code": claude_root, "cursor": cursor_root},
    )
    children_of = {}
    for edge in g.edges:
        children_of.setdefault(edge.parent, []).append(edge.child)
    deps = {}
    for node in g.nodes.values():
        if node.kind != "mcp_server" or node.ref is None:
            continue
        children = [g.nodes[c] for c in children_of.get(node.key, [])]
        deps[node.key.split("#")[0]] = sorted(
            str(child.ref.name)
            for child in children
            if child.kind == "package" and child.ref is not None
        )
    assert deps == {
        "endpoint/.mcp.json": ["left-pad"],
        "endpoint-cursor/mcp.json": ["right-pad"],
    }


def test_endpoint_two_hosts_coexist_under_one_target(tmp_path):
    claude_root = _minimal_claude_install_root(tmp_path)
    cursor_root = tmp_path / "cursor"
    (cursor_root / "skills").mkdir(parents=True)
    (cursor_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    g = build_graph(
        claude_root,
        mode="endpoint",
        host_config_roots={"claude-code": claude_root, "cursor": cursor_root},
    )
    targets = [n for n in g.nodes.values() if n.kind == "target"]
    assert len(targets) == 1
    keys = [n.key for n in g.nodes.values()]
    assert len(keys) == len(set(keys))
    assert any(k.startswith("endpoint/") for k in keys)
    assert any(k.startswith("endpoint-cursor/") for k in keys)


def _agent_nodes(g):
    return sorted(
        (n for n in g.nodes.values() if n.ref and n.ref.extra.get("component_type") == "agent"),
        key=lambda n: n.key,
    )


def test_endpoint_subagents_found_under_nonstandard_explicit_root(tmp_path, monkeypatch):
    # An explicit override root is an arbitrary path; its agents dir is
    # <config_root>/agents, never rediscovered via dot-directory names.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude-here"))
    cursor_root = tmp_path / "cursor"  # deliberately NOT named ".cursor"
    (cursor_root / "agents").mkdir(parents=True)
    (cursor_root / "agents" / "helper.md").write_text("---\nname: helper\n---\nh\n")
    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})
    agents = _agent_nodes(g)
    assert len(agents) == 1
    assert agents[0].ref.extra["runtime_hosts"] == ["cursor"]
    assert agents[0].key.startswith("endpoint-cursor/agents/helper.md")


def test_endpoint_cursor_only_claude_compat_subagent_has_stable_key(tmp_path, monkeypatch):
    # Cursor reads Claude's agents directory even when Claude Code is not
    # selected. The source lies under a named auxiliary root, so the occurrence
    # key must be reproducible rather than machine-absolute.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    claude_compat = tmp_path / "home" / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_compat))
    (claude_compat / "agents").mkdir(parents=True)
    (claude_compat / "agents" / "helper.md").write_text("---\nname: helper\n---\nh\n")
    cursor_root = tmp_path / "elsewhere" / "cursor-config"
    cursor_root.mkdir(parents=True)
    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})
    agents = _agent_nodes(g)
    assert len(agents) == 1
    assert agents[0].ref.extra["runtime_hosts"] == ["cursor"]
    assert "endpoint-claude-compat/agents/helper.md" in agents[0].key
    assert str(claude_compat) not in agents[0].key


def test_endpoint_dual_host_subagent_occurrences_global_scope(tmp_path):
    claude_root = _minimal_claude_install_root(tmp_path)
    (claude_root / "agents" / "helper.md").write_text("---\nname: helper\n---\nh\n")
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()
    roots = {"claude-code": claude_root, "cursor": cursor_root}

    g = build_graph(claude_root, mode="endpoint", host_config_roots=roots)
    shared = [n for n in _agent_nodes(g) if n.ref.name == "helper"]
    assert len(shared) == 1
    assert shared[0].ref.extra["runtime_hosts"] == ["claude-code", "cursor"]

    # A Cursor override at the same relative path splits the occurrence in two:
    # Cursor never reads Claude's file when it has its own copy.
    (cursor_root / "agents").mkdir()
    (cursor_root / "agents" / "helper.md").write_text("---\nname: helper\n---\nh\n")
    g2 = build_graph(claude_root, mode="endpoint", host_config_roots=roots)
    split = [n for n in _agent_nodes(g2) if n.ref.name == "helper"]
    assert [n.ref.extra["runtime_hosts"] for n in split] == [["cursor"], ["claude-code"]]
    assert split[0].key.startswith("endpoint-cursor/agents/helper.md")
    assert split[1].key.startswith("endpoint/agents/helper.md")


def test_endpoint_dual_host_subagent_occurrences_project_scope(tmp_path):
    claude_root = _minimal_claude_install_root(tmp_path)
    cursor_root = tmp_path / "cursor"
    cursor_root.mkdir()
    project = tmp_path / "project"
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\n---\nh\n")
    roots = {"claude-code": claude_root, "cursor": cursor_root}

    g = build_graph(claude_root, mode="endpoint", project_root=project, host_config_roots=roots)
    shared = [n for n in _agent_nodes(g) if n.ref.name == "helper"]
    assert len(shared) == 1
    assert shared[0].ref.extra["runtime_hosts"] == ["claude-code", "cursor"]
    assert shared[0].key.startswith("project/.claude/agents/helper.md")

    (project / ".cursor" / "agents").mkdir(parents=True)
    (project / ".cursor" / "agents" / "helper.md").write_text("---\nname: helper\n---\nh\n")
    g2 = build_graph(claude_root, mode="endpoint", project_root=project, host_config_roots=roots)
    split = [n for n in _agent_nodes(g2) if n.ref.name == "helper"]
    assert sorted(n.ref.extra["runtime_hosts"] for n in split) == [["claude-code"], ["cursor"]]


def test_endpoint_auxiliary_root_mcp_gets_no_launch_resolution(tmp_path, monkeypatch):
    # Contract item 3: auxiliary roots contribute no name index and no launch
    # resolution. A compatibility-read agent under `endpoint-claude-compat/`
    # declaring `npx server` must NOT bind the same-named package that exists
    # under the CURSOR root — the cross-root misattribution item 4 forbids.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    claude_compat = tmp_path / "home" / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_compat))
    (claude_compat / "agents").mkdir(parents=True)
    (claude_compat / "agents" / "helper.md").write_text(
        '---\nname: helper\nmcpServers:\n  local: {"command": "npx", "args": ["server"]}\n---\nh\n'
    )
    cursor_root = tmp_path / "cursor"
    (cursor_root / "server").mkdir(parents=True)
    (cursor_root / "server" / "package.json").write_text(
        '{"name": "server", "version": "1.0.0", "dependencies": {"right-pad": "2.0.0"}}'
    )

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})
    mcps = [n for n in g.nodes.values() if n.kind == "mcp_server"]
    assert len(mcps) == 1
    assert mcps[0].key.startswith("endpoint-claude-compat/agents/helper.md")
    children = {e.child for e in g.edges if e.parent == mcps[0].key}
    assert children == set()
    assert not [n for n in g.nodes.values() if n.kind == "package"]


def _cursor_endpoint_fixture(tmp_path):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    (cursor_root / "skills" / "global-skill").mkdir(parents=True)
    (cursor_root / "skills" / "global-skill" / "SKILL.md").write_text(
        "---\nname: global-skill\ndescription: d\n---\nrun\n"
    )
    (home / ".agents" / "skills" / "shared-skill").mkdir(parents=True)
    (home / ".agents" / "skills" / "shared-skill" / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: d\n---\nrun\n"
    )
    (cursor_root / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    native_root = cursor_root / "plugins" / "local" / "demo"
    (native_root / ".cursor-plugin").mkdir(parents=True)
    (native_root / ".cursor-plugin" / "plugin.json").write_text(
        '{"name": "demo", "hooks": {"postToolUse": [{"command": "echo done"}]}}'
    )
    (native_root / "skills" / "bundled-skill").mkdir(parents=True)
    (native_root / "skills" / "bundled-skill" / "SKILL.md").write_text(
        "---\nname: bundled-skill\ndescription: d\n---\nrun\n"
    )
    (native_root / "skills" / "bundled-skill" / "package.json").write_text(
        '{"dependencies": {"left-pad": "1.0.0"}}'
    )
    (native_root / "mcp.json").write_text(
        '{"mcpServers": {"bundled-mcp": {"command": "npx", "args": ["bundled-mcp@1.0.0"]}}}'
    )
    (native_root / "commands").mkdir()
    (native_root / "commands" / "plugin-cmd.md").write_text("run\n")
    (native_root / "agents").mkdir()
    (native_root / "agents" / "plugin-agent.md").write_text("---\nname: plugin-agent\n---\nbody\n")
    open_root = cursor_root / "plugins" / "local" / "open-demo"
    open_root.mkdir(parents=True)
    (open_root / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",'
        ' "name": "open-demo"}'
    )
    (open_root / "skills" / "ap-skill").mkdir(parents=True)
    (open_root / "skills" / "ap-skill" / "SKILL.md").write_text(
        "---\nname: ap-skill\ndescription: d\n---\nrun\n"
    )
    (open_root / "skills" / "ap-skill" / "package.json").write_text(
        '{"dependencies": {"left-pad": "1.0.0"}}'
    )
    (open_root / "mcp.json").write_text(
        '{"mcpServers": {"open-mcp": {"command": "npx", "args": ["open-mcp@1.0.0"]}}}'
    )
    (open_root / "commands").mkdir()
    (open_root / "commands" / "not-portable.md").write_text("run\n")
    broken_dir = cursor_root / "plugins" / "local" / "broken" / ".cursor-plugin"
    broken_dir.mkdir(parents=True)
    (broken_dir / "plugin.json").write_text("{not json")
    project = tmp_path / "project"
    (project / ".cursor" / "skills" / "proj-skill").mkdir(parents=True)
    (project / ".cursor" / "skills" / "proj-skill" / "SKILL.md").write_text(
        "---\nname: proj-skill\ndescription: d\n---\nrun\n"
    )
    (project / ".agents" / "skills" / "proj-shared").mkdir(parents=True)
    (project / ".agents" / "skills" / "proj-shared" / "SKILL.md").write_text(
        "---\nname: proj-shared\ndescription: d\n---\nrun\n"
    )
    (project / ".cursor" / "commands").mkdir(parents=True)
    (project / ".cursor" / "commands" / "deploy.md").write_text("run\n")
    return home, cursor_root, project


def test_endpoint_cursor_seed_endpoint_composes_all_surfaces(tmp_path, monkeypatch):
    home, cursor_root, project = _cursor_endpoint_fixture(tmp_path)
    monkeypatch.setenv("HOME", str(home))  # pins Path.home() -> ~/.agents/skills
    g = build_graph(
        cursor_root,
        mode="endpoint",
        project_root=project,
        host_config_roots={"cursor": cursor_root},
    )
    skill_names = {n.ref.name for n in g.nodes.values() if n.kind == "skill" and n.ref}
    assert skill_names == {
        "global-skill",
        "shared-skill",
        "proj-skill",
        "proj-shared",
        "bundled-skill",
        "ap-skill",
    }
    assert any(n.kind == "mcp_server" for n in g.nodes.values())
    assert any(n.kind == "command" for n in g.nodes.values())
    plugin_names = {n.ref.name for n in g.nodes.values() if n.kind == "plugin" and n.ref}
    assert plugin_names == {"demo", "open-demo"}  # "broken" skipped, scan not aborted
    for n in g.nodes.values():
        if n.kind == "plugin" and n.ref is not None:
            assert "enabled" not in n.ref.extra
            assert "active" not in n.ref.extra
    parent_of = {e.child: e.parent for e in g.edges}
    plugins = {n.ref.name: n for n in g.nodes.values() if n.kind == "plugin" and n.ref}

    def _children_by_kind(plugin_node):
        kids = [n for n in g.nodes.values() if parent_of.get(n.key) == plugin_node.key]
        return {n.kind for n in kids}, kids

    demo_kinds, demo_kids = _children_by_kind(plugins["demo"])
    # Native bundle fully realized under the plugin, repo-parity: every
    # surface plus the inline camelCase hook.
    assert {"skill", "mcp_server", "command", "agent", "hook"} <= demo_kinds
    open_kinds, open_kids = _children_by_kind(plugins["open-demo"])
    # Agent Plugins closed surface: skills+MCP only — the commands/ dir
    # produces no node (would fail if endpoint realization reused the
    # native descent).
    assert open_kinds == {"skill", "mcp_server"}
    for kid in demo_kids + open_kids:
        assert kid.ref is not None and kid.ref.extra["runtime_hosts"] == ["cursor"]
    # Bundled skills keep their dependency-manifest chains in endpoint mode,
    # same as repo mode: each bundled skill node has a package child.
    for skill_node in [n for n in demo_kids + open_kids if n.kind == "skill"]:
        assert skill_node.ref is not None
        dep_kinds = {n.kind for n in g.nodes.values() if parent_of.get(n.key) == skill_node.key}
        assert "package" in dep_kinds, f"{skill_node.ref.name} lost its dep chain"


def test_endpoint_cursor_project_skills_honor_project_gitignore(tmp_path, monkeypatch):
    # Regression guard: Cursor's endpoint project-scoped skill roots
    # (.cursor/skills, .agents/skills) must honor the project's .gitignore,
    # parity with Claude's endpoint_seeds.claude_code._add_project_skills call
    # — a skill under an ignored path (e.g. a worktree) must not be
    # inventoried just because it's discovered via Cursor instead of Claude.
    home, cursor_root, project = _cursor_endpoint_fixture(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    (project / ".gitignore").write_text(".cursor/\n.agents/\n")
    g = build_graph(
        cursor_root,
        mode="endpoint",
        project_root=project,
        host_config_roots={"cursor": cursor_root},
    )
    skill_names = {n.ref.name for n in g.nodes.values() if n.kind == "skill" and n.ref}
    assert "proj-skill" not in skill_names
    assert "proj-shared" not in skill_names
    # Non-project-scoped skill roots are unaffected by the project's .gitignore.
    assert {"global-skill", "shared-skill", "bundled-skill", "ap-skill"} <= skill_names


def test_endpoint_dev_linked_dual_format_dir_realizes_native_only(tmp_path, monkeypatch):
    # A dev-linked dir carrying BOTH manifests: the native
    # `.cursor-plugin/plugin.json` and a schema-tagged root `plugin.json`.
    # Realizing both walks the same `skills/` and the same default root
    # `mcp.json`, parenting one occurrence under two plugins — which aborts
    # the whole scan with GraphInvariantError. Native format wins when both
    # manifests share a directory (repo mode's rule), so exactly one plugin
    # node exists for that dir and it is the native one.
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    dual = cursor_root / "plugins" / "local" / "dual"
    (dual / ".cursor-plugin").mkdir(parents=True)
    (dual / ".cursor-plugin" / "plugin.json").write_text('{"name": "dual-native"}')
    (dual / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "dual-open",
            }
        )
    )
    (dual / "skills" / "shared").mkdir(parents=True)
    (dual / "skills" / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: d\n---\nrun\n"
    )
    (dual / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    (dual / "commands").mkdir()
    (dual / "commands" / "deploy.md").write_text("run\n")
    monkeypatch.setenv("HOME", str(home))  # pins Path.home() -> ~/.agents/skills

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin"]
    assert len(plugin_nodes) == 1
    assert plugin_nodes[0].ref is not None
    assert plugin_nodes[0].ref.name == "dual-native"
    parent_of = {e.child: e.parent for e in g.edges}
    kids = [n for n in g.nodes.values() if parent_of.get(n.key) == plugin_nodes[0].key]
    assert len([n for n in kids if n.kind == "skill"]) == 1
    # The command node only exists under the native descent — the closed
    # Agent Plugins realization never enumerates commands/.
    assert "command" in {n.kind for n in kids}


def test_endpoint_cached_native_plugin_seeds_with_marketplace_dir(tmp_path, monkeypatch):
    # ADR-0045 Decision #7: `plugins/cache/<marketplace>/<name>/<sha>/` gated on
    # `.cache-complete`, native format, bundled children nested under it.
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "alpha" / "deadbeef"
    (cached / ".cursor-plugin").mkdir(parents=True)
    (cached / ".cursor-plugin" / "plugin.json").write_text('{"name": "alpha"}')
    (cached / "skills" / "cached-skill").mkdir(parents=True)
    (cached / "skills" / "cached-skill" / "SKILL.md").write_text(
        "---\nname: cached-skill\ndescription: d\n---\nrun\n"
    )
    (cached / "mcp.json").write_text(
        '{"mcpServers": {"cached-mcp": {"command": "npx", "args": ["cached-mcp@1.0.0"]}}}'
    )
    (cached / ".cache-complete").write_text("")
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin" and n.ref]
    assert len(plugin_nodes) == 1
    plugin_node = plugin_nodes[0]
    assert plugin_node.ref is not None
    assert plugin_node.ref.name == "alpha"
    assert plugin_node.ref.extra["runtime_hosts"] == ["cursor"]
    assert plugin_node.ref.extra["cursor_marketplace_dir"] == "cursor-public"
    assert "enabled" not in plugin_node.ref.extra
    assert "active" not in plugin_node.ref.extra
    assert plugin_node.key.startswith("endpoint-cursor/")
    parent_of = {e.child: e.parent for e in g.edges}
    kids = [n for n in g.nodes.values() if parent_of.get(n.key) == plugin_node.key]
    assert {"skill", "mcp_server"} <= {n.kind for n in kids}


def test_endpoint_cached_agent_plugins_format_seeds_with_marketplace_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "beta" / "cafef00d"
    cached.mkdir(parents=True)
    (cached / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "beta",
            }
        )
    )
    (cached / "skills" / "ap-skill").mkdir(parents=True)
    (cached / "skills" / "ap-skill" / "SKILL.md").write_text(
        "---\nname: ap-skill\ndescription: d\n---\nrun\n"
    )
    (cached / "mcp.json").write_text(
        '{"mcpServers": {"open-mcp": {"command": "npx", "args": ["open-mcp@1.0.0"]}}}'
    )
    (cached / "commands").mkdir()
    (cached / "commands" / "not-portable.md").write_text("run\n")
    (cached / ".cache-complete").write_text("")
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin" and n.ref]
    assert len(plugin_nodes) == 1
    plugin_node = plugin_nodes[0]
    assert plugin_node.ref is not None
    assert plugin_node.ref.name == "beta"
    assert plugin_node.ref.extra["cursor_marketplace_dir"] == "cursor-public"
    assert "enabled" not in plugin_node.ref.extra
    parent_of = {e.child: e.parent for e in g.edges}
    kids = [n for n in g.nodes.values() if parent_of.get(n.key) == plugin_node.key]
    # Closed Agent Plugins realization: skills+MCP only, no commands/.
    assert {n.kind for n in kids} == {"skill", "mcp_server"}


def test_endpoint_cached_version_dir_without_cache_complete_seeds_nothing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "gamma" / "incomplete-sha"
    (cached / ".cursor-plugin").mkdir(parents=True)
    (cached / ".cursor-plugin" / "plugin.json").write_text('{"name": "gamma"}')
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    assert not [n for n in g.nodes.values() if n.kind == "plugin"]


def test_endpoint_cached_manifest_less_bundle_synthesizes_presence_only_ref(tmp_path, monkeypatch):
    # ADR-0045 Decision #7: the "granola" real-world case — no manifest in either
    # format, only skills/ and commands/, but Cursor still loads it.
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "granola" / "somesha"
    (cached / "skills" / "granola-skill").mkdir(parents=True)
    (cached / "skills" / "granola-skill" / "SKILL.md").write_text(
        "---\nname: granola-skill\ndescription: d\n---\nrun\n"
    )
    (cached / "commands").mkdir()
    (cached / "commands" / "granola-cmd.md").write_text("run\n")
    (cached / ".cache-complete").write_text("")
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin" and n.ref]
    assert len(plugin_nodes) == 1
    plugin_node = plugin_nodes[0]
    assert plugin_node.ref is not None
    assert plugin_node.ref.name == "granola"
    # Unqualified `plugin/granola` identity is nulled by
    # canonical_component_identity (no verified marketplace) — ADR-0045 Decision #7
    # point 4 — so the finalized ref carries no cross-BOM identity here.
    assert plugin_node.ref.component_identity is None
    assert plugin_node.ref.extra["manifest"] == "absent"
    assert plugin_node.ref.extra["cursor_marketplace_dir"] == "cursor-public"
    assert plugin_node.ref.extra["runtime_hosts"] == ["cursor"]
    assert "enabled" not in plugin_node.ref.extra
    assert "active" not in plugin_node.ref.extra
    parent_of = {e.child: e.parent for e in g.edges}
    kids = [n for n in g.nodes.values() if parent_of.get(n.key) == plugin_node.key]
    assert {n.kind for n in kids} == {"skill", "command"}


def test_endpoint_cached_dual_format_dir_realizes_native_only(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "dual" / "somesha"
    (cached / ".cursor-plugin").mkdir(parents=True)
    (cached / ".cursor-plugin" / "plugin.json").write_text('{"name": "dual-native"}')
    (cached / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "dual-open",
            }
        )
    )
    (cached / ".cache-complete").write_text("")
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin"]
    assert len(plugin_nodes) == 1
    assert plugin_nodes[0].ref is not None
    assert plugin_nodes[0].ref.name == "dual-native"
    assert plugin_nodes[0].ref.extra["cursor_marketplace_dir"] == "cursor-public"


def test_endpoint_dev_linked_plugin_ref_carries_no_marketplace_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    dev_linked = cursor_root / "plugins" / "local" / "demo"
    (dev_linked / ".cursor-plugin").mkdir(parents=True)
    (dev_linked / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin" and n.ref]
    assert len(plugin_nodes) == 1
    assert plugin_nodes[0].ref is not None
    assert "cursor_marketplace_dir" not in plugin_nodes[0].ref.extra


def test_endpoint_dev_linked_native_plugin_carries_user_scope(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    dev_linked = cursor_root / "plugins" / "local" / "demo"
    (dev_linked / ".cursor-plugin").mkdir(parents=True)
    (dev_linked / ".cursor-plugin" / "plugin.json").write_text('{"name": "demo"}')
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin" and n.ref]
    assert len(plugin_nodes) == 1
    plugin_ref = plugin_nodes[0].ref
    assert plugin_ref is not None
    assert plugin_ref.extra["scope"] == "user"


def test_endpoint_dev_linked_agent_plugins_carries_user_scope(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    dev_linked = cursor_root / "plugins" / "local" / "demo"
    dev_linked.mkdir(parents=True)
    (dev_linked / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin" and n.ref]
    assert len(plugin_nodes) == 1
    plugin_ref = plugin_nodes[0].ref
    assert plugin_ref is not None
    assert plugin_ref.extra["scope"] == "user"


def test_endpoint_cached_native_plugin_carries_user_scope(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "alpha" / "deadbeef"
    (cached / ".cursor-plugin").mkdir(parents=True)
    (cached / ".cursor-plugin" / "plugin.json").write_text('{"name": "alpha"}')
    (cached / ".cache-complete").write_text("")
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin" and n.ref]
    assert len(plugin_nodes) == 1
    plugin_ref = plugin_nodes[0].ref
    assert plugin_ref is not None
    assert plugin_ref.extra["scope"] == "user"
    assert plugin_ref.extra["cursor_marketplace_dir"] == "cursor-public"


def test_endpoint_cached_manifest_less_bundle_carries_user_scope(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cursor_root = home / ".cursor"
    cached = cursor_root / "plugins" / "cache" / "cursor-public" / "granola" / "somesha"
    (cached / "skills" / "granola-skill").mkdir(parents=True)
    (cached / "skills" / "granola-skill" / "SKILL.md").write_text(
        "---\nname: granola-skill\ndescription: d\n---\nrun\n"
    )
    (cached / ".cache-complete").write_text("")
    monkeypatch.setenv("HOME", str(home))

    g = build_graph(cursor_root, mode="endpoint", host_config_roots={"cursor": cursor_root})

    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin" and n.ref]
    assert len(plugin_nodes) == 1
    plugin_ref = plugin_nodes[0].ref
    assert plugin_ref is not None
    assert plugin_ref.extra["scope"] == "user"


def test_repo_cursor_plugin_carries_no_scope(tmp_path):
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    g = build_graph(tmp_path, mode="repo", hosts=["claude-code", "cursor"])
    plugin_nodes = [n for n in g.nodes.values() if n.kind == "plugin"]
    assert len(plugin_nodes) == 1
    plugin_ref = plugin_nodes[0].ref
    assert plugin_ref is not None
    assert "scope" not in plugin_ref.extra


def test_shared_agents_skills_root_is_home_scoped_not_override_relative(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".agents" / "skills" / "home-shared").mkdir(parents=True)
    (home / ".agents" / "skills" / "home-shared" / "SKILL.md").write_text(
        "---\nname: home-shared\ndescription: d\n---\nrun\n"
    )
    monkeypatch.setenv("HOME", str(home))
    override_root = tmp_path / "elsewhere" / "cursor-config"  # NOT under home
    (override_root / "skills").mkdir(parents=True)
    # A .agents sibling of the override — must NOT be scanned.
    (tmp_path / "elsewhere" / ".agents" / "skills" / "decoy").mkdir(parents=True)
    (tmp_path / "elsewhere" / ".agents" / "skills" / "decoy" / "SKILL.md").write_text(
        "---\nname: decoy\ndescription: d\n---\nrun\n"
    )
    g = build_graph(override_root, mode="endpoint", host_config_roots={"cursor": override_root})
    skill_names = {n.ref.name for n in g.nodes.values() if n.kind == "skill" and n.ref}
    assert "home-shared" in skill_names
    assert "decoy" not in skill_names
    shared = next(
        n for n in g.nodes.values() if n.kind == "skill" and n.ref and n.ref.name == "home-shared"
    )
    assert "endpoint-shared-agents/skills/home-shared/SKILL.md" in shared.key
    assert str(home) not in shared.key
    assert str(override_root) not in shared.key
