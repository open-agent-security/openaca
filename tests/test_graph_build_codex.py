"""Codex composition (plan 043 Tasks 6-7).

Repo mode reuses `descend(surface=CODEX_SURFACE)` — Codex's tree surface is
expressible in ADR-0053's existing descriptor. Two surfaces sit outside it and
are added alongside rather than forking the walk: `.codex/hooks.json` (Claude
Code has no repo-mode standalone hooks surface, so `RepoSurface` has no field
for one) and MCP servers, which live inside `.codex/config.toml` as one TOML
table among four rather than in a dedicated manifest.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tools.graph_build import build_codex_declared_graph

HOOKS = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}


def _agent(scan_root: Path):
    return SimpleNamespace(
        source="declared",
        scan_root=str(scan_root),
        bom_ref="agent/codex",
        root_label="codex",
        config_root=None,
        project_root=None,
    )


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".codex" / "skills" / "demo").mkdir(parents=True)
    (tmp_path / ".codex" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\n---\nSkill body.\n", encoding="utf-8"
    )
    (tmp_path / ".codex" / "hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")
    (tmp_path / ".codex" / "config.toml").write_text(
        '[mcp_servers.svc]\ncommand = "npx"\nargs = ["@scope/tool@1.2.3"]\n', encoding="utf-8"
    )
    return tmp_path


def _kinds(graph) -> set[str]:
    return {n.kind for n in graph.nodes.values()}


def test_declared_graph_carries_skill_hook_and_mcp(tmp_path):
    graph = build_codex_declared_graph(_agent(_repo(tmp_path)))
    graph.validate()

    assert {"skill", "hook", "mcp_server"} <= _kinds(graph)


def test_agents_md_is_never_a_node(tmp_path):
    """Instruction files are not configuration — the same rule Claude Code
    applies to its own CLAUDE.md (spec: Not configuration)."""
    root = _repo(tmp_path)
    (root / "AGENTS.md").write_text("# project instructions\n", encoding="utf-8")

    graph = build_codex_declared_graph(_agent(root))

    assert not any("AGENTS.md" in key for key in graph.nodes)


def test_a_disabled_project_mcp_server_is_still_inventoried(tmp_path):
    """ADR-0055: `enabled` records state, it does not gate membership."""
    root = _repo(tmp_path)
    (root / ".codex" / "config.toml").write_text(
        '[mcp_servers.svc]\ncommand = "true"\nenabled = false\n', encoding="utf-8"
    )

    graph = build_codex_declared_graph(_agent(root))
    servers = _refs(graph, "mcp_server")

    assert len(servers) == 1
    assert servers[0].extra["enabled"] is False


def test_a_repo_with_no_codex_surface_yields_only_the_target(tmp_path):
    graph = build_codex_declared_graph(_agent(tmp_path))

    assert _kinds(graph) == {"target"}


def test_malformed_config_toml_warns_rather_than_raising(tmp_path):
    """A broken config must not abort the whole scan."""
    root = _repo(tmp_path)
    (root / ".codex" / "config.toml").write_text("{ not toml", encoding="utf-8")
    warnings: list[str] = []

    graph = build_codex_declared_graph(_agent(root), warnings=warnings)

    assert "skill" in _kinds(graph), "the rest of the tree still composes"
    assert any("config.toml" in w for w in graph.warnings + warnings)


def test_malformed_hooks_json_warns_rather_than_raising(tmp_path):
    root = _repo(tmp_path)
    (root / ".codex" / "hooks.json").write_text("{ not json", encoding="utf-8")
    warnings: list[str] = []

    graph = build_codex_declared_graph(_agent(root), warnings=warnings)

    assert "skill" in _kinds(graph)
    assert any("hooks.json" in w for w in graph.warnings + warnings)


def test_a_nested_project_codex_dir_is_composed(tmp_path):
    """Codex reads project config at any depth, as `descend` already walks."""
    nested = tmp_path / "packages" / "app"
    nested.mkdir(parents=True)
    (nested / ".codex" / "skills" / "nested").mkdir(parents=True)
    (nested / ".codex" / "skills" / "nested" / "SKILL.md").write_text(
        "---\nname: nested\n---\nN\n", encoding="utf-8"
    )

    graph = build_codex_declared_graph(_agent(tmp_path))

    assert "skill" in _kinds(graph)


def test_a_skill_nested_under_the_skills_root_is_still_composed(tmp_path):
    """`CODEX_MANIFEST_REGISTRY` matches `**/.codex/skills/**/SKILL.md`
    (recursive, per docs/specs/codex-agent-kind.md's Skills row: "Traversal:
    recursive") — composition must accept the same nesting, or a skill the
    registry counts toward `source_unit_count` never reaches the graph."""
    root = _repo(tmp_path)
    nested = root / ".codex" / "skills" / "group" / "tool"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\nname: tool\n---\nNested.\n", encoding="utf-8")

    graph = build_codex_declared_graph(_agent(root))

    assert _skill_names(graph) == {"demo", "tool"}


def test_plugin_owned_codex_config_and_hooks_are_excluded_from_composition(tmp_path):
    """A realized plugin's own `.codex/config.toml`/`.codex/hooks.json`
    fixtures (e.g. an example bundled inside the plugin) belong to the
    plugin's subtree, not the target — the single-parent invariant declared
    evidence detection and registry parse-count accounting already apply via
    `CODEX_SURFACE.excludes_plugin_owned_content`. The raw `rglob` walks that
    add project-scope MCP servers/hooks must honor the same exclusion, or a
    bundled fixture is composed as if the project declared it directly."""
    root = _repo(tmp_path)
    plugin_root = root / "vendor" / "demo-plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo-plugin"}), encoding="utf-8"
    )
    (plugin_root / "examples" / ".codex").mkdir(parents=True)
    (plugin_root / "examples" / ".codex" / "config.toml").write_text(
        '[mcp_servers.phantom]\ncommand = "true"\n', encoding="utf-8"
    )
    (plugin_root / "examples" / ".codex" / "hooks.json").write_text(
        json.dumps(HOOKS), encoding="utf-8"
    )

    graph = build_codex_declared_graph(_agent(root))

    mcp_names = {
        n.ref.extra["component_path"][0]["name"]
        for n in graph.nodes.values()
        if n.kind == "mcp_server" and n.ref is not None
    }
    assert mcp_names == {"svc"}
    hook_count = sum(1 for n in graph.nodes.values() if n.kind == "hook")
    assert hook_count == 1


# --- Installed composition (Task 7) ----------------------------------------

from tools.graph_build import build_codex_installed_graph  # noqa: E402

CONFIG = """
[marketplaces.mkt]
source_type = "git"
source = "https://example.test/mkt.git"
last_revision = "deadbeef"

[plugins."codexpl@mkt"]
enabled = true

[plugins."claudepl@mkt"]
enabled = true

[plugins."offpl@mkt"]
enabled = false

[mcp_servers.user_svc]
command = "npx"
args = ["@scope/tool@1.2.3"]
"""


def _bundle(root, mkt, name, ver, manifest_dir, manifest=None):
    d = root / "plugins" / "cache" / mkt / name / ver
    (d / manifest_dir).mkdir(parents=True)
    body = manifest if manifest is not None else json.dumps({"name": name, "version": ver})
    (d / manifest_dir / "plugin.json").write_text(body, encoding="utf-8")
    (d / "skills" / f"{name}-skill").mkdir(parents=True)
    (d / "skills" / f"{name}-skill" / "SKILL.md").write_text(
        f"---\nname: {name}-skill\n---\nBundled.\n", encoding="utf-8"
    )
    return d


def _home(tmp_path: Path) -> Path:
    root = tmp_path / "codex-home"
    root.mkdir()
    (root / "config.toml").write_text(CONFIG, encoding="utf-8")

    _bundle(root, "mkt", "codexpl", "1.0.0", ".codex-plugin")
    _bundle(root, "mkt", "claudepl", "1.0.0", ".claude-plugin")
    _bundle(root, "mkt", "offpl", "1.0.0", ".codex-plugin")

    (root / "skills" / "user-skill").mkdir(parents=True)
    (root / "skills" / "user-skill" / "SKILL.md").write_text(
        "---\nname: user-skill\n---\nUser.\n", encoding="utf-8"
    )
    # Vendor built-ins, marked structurally. The inner skill has an innocuous
    # name on purpose: exclusion must not depend on recognising it.
    sysdir = root / "skills" / ".system"
    (sysdir / "helpful-tool").mkdir(parents=True)
    (sysdir / ".codex-system-skills.marker").write_text("x", encoding="utf-8")
    (sysdir / "helpful-tool" / "SKILL.md").write_text(
        "---\nname: helpful-tool\n---\nVendor.\n", encoding="utf-8"
    )

    (root / "agents").mkdir()
    (root / "agents" / "probe.toml").write_text('name = "probe"\n', encoding="utf-8")

    (root / "rules").mkdir()
    (root / "rules" / "default.rules").write_text(
        'prefix_rule(pattern=["git", "commit"], decision="allow")\n', encoding="utf-8"
    )
    return root


def _refs(graph, kind):
    """Refs of one node kind, with `None` narrowed away for the type checker."""
    return [n.ref for n in graph.nodes.values() if n.kind == kind and n.ref is not None]


def _plugins(graph):
    return {r.name: r for r in _refs(graph, "plugin")}


def _skill_names(graph):
    return {r.name for r in _refs(graph, "skill")}


def _server_named(graph, name):
    return [r for r in _refs(graph, "mcp_server") if r.extra["component_path"][0]["name"] == name]


def test_every_cached_bundle_is_inventoried_with_explicit_enable_state(tmp_path):
    """ADR-0055. Claude Code walks the enable map and cannot see a disabled
    plugin at all; Codex walks the cache, which is why this is forked."""
    graph = build_codex_installed_graph(_home(tmp_path))
    plugins = _plugins(graph)

    assert set(plugins) == {"codexpl", "claudepl", "offpl"}
    assert plugins["offpl"].extra["enabled"] is False
    assert plugins["codexpl"].extra["enabled"] is True
    assert plugins["claudepl"].extra["enabled"] is True


def test_a_claude_plugin_only_bundle_realizes_via_the_fallback_candidate(tmp_path):
    graph = build_codex_installed_graph(_home(tmp_path))

    assert "claudepl" in _plugins(graph)


def test_marketplace_identity_comes_from_the_registry(tmp_path):
    graph = build_codex_installed_graph(_home(tmp_path))

    assert _plugins(graph)["codexpl"].component_identity == "plugin/mkt/codexpl"
    assert _plugins(graph)["codexpl"].extra["marketplace"] == "mkt"
    assert _plugins(graph)["codexpl"].extra["last_revision"] == "deadbeef"


def test_an_unregistered_marketplace_segment_grants_no_cross_bom_identity(tmp_path):
    """A cache-path segment is not provenance."""
    root = _home(tmp_path)
    _bundle(root, "ghost", "orphan", "1.0.0", ".codex-plugin")

    graph = build_codex_installed_graph(root)
    ref = _plugins(graph)["orphan"]

    assert "marketplace" not in ref.extra
    assert ref.component_identity != "plugin/ghost/orphan"
    assert any("no [marketplaces.ghost] entry" in w for w in graph.warnings)


def test_bundled_skill_identity_cascades_from_the_plugin(tmp_path):
    graph = build_codex_installed_graph(_home(tmp_path))
    bundled = [r for r in _refs(graph, "skill") if "codexpl-skill" in str(r.name)]

    assert bundled, "the bundled skill should be composed"
    assert bundled[0].component_identity is not None


def test_system_skills_are_excluded_by_marker(tmp_path):
    graph = build_codex_installed_graph(_home(tmp_path))
    skill_names = _skill_names(graph)

    assert "user-skill" in skill_names
    assert "helpful-tool" not in skill_names, "vendor built-ins are not user composition"


def test_a_direct_skill_nested_under_the_install_root_is_still_composed(tmp_path):
    """`docs/specs/codex-agent-kind.md`'s Skills row documents `<root>/skills/`
    as recursive for both declared and installed sources. `CODEX_ENDPOINT`
    routes install-root skills through the shared `_add_direct_endpoint_skills`
    walk, so a nested layout like `$CODEX_HOME/skills/group/tool/SKILL.md`
    must still yield a node, not just the immediate-child case already covered
    by `user-skill`."""
    root = _home(tmp_path)
    nested = root / "skills" / "group" / "tool"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\nname: tool\n---\nNested.\n", encoding="utf-8")

    graph = build_codex_installed_graph(root)

    assert "tool" in _skill_names(graph)


def test_subagents_are_composed_from_toml(tmp_path):
    graph = build_codex_installed_graph(_home(tmp_path))
    agents = {r.name for r in _refs(graph, "agent")}

    assert agents == {"probe"}


def test_a_configured_plugin_with_no_cache_bundle_warns_but_is_not_a_node(tmp_path):
    root = _home(tmp_path)
    (root / "config.toml").write_text(
        CONFIG + '\n[plugins."phantom@mkt"]\nenabled = true\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)

    assert "phantom" not in _plugins(graph)
    assert any("phantom@mkt is configured but missing" in w for w in graph.warnings)


def test_a_cached_bundle_with_no_enable_record_defaults_to_enabled_and_warns(tmp_path):
    root = _home(tmp_path)
    _bundle(root, "mkt", "unlisted", "2.0.0", ".codex-plugin")

    graph = build_codex_installed_graph(root)

    assert _plugins(graph)["unlisted"].extra["enabled"] is True
    assert any("no enable-map record" in w for w in graph.warnings)


def test_unparsed_rules_reach_the_warnings_list(tmp_path):
    """Posture-only surfaces still owe coverage — the warnings list is what
    carries them into `evidence_gaps` for all three commands."""
    root = _home(tmp_path)
    (root / "rules" / "default.rules").write_text("suffix_rule(nope=1)\n", encoding="utf-8")
    warnings: list[str] = []

    build_codex_installed_graph(root, warnings=warnings)

    assert any("unparsed rule(s)" in w for w in warnings)


def test_a_clean_rules_file_adds_no_warning(tmp_path):
    warnings: list[str] = []
    build_codex_installed_graph(_home(tmp_path), warnings=warnings)

    assert not any("unparsed rule(s)" in w for w in warnings)


def test_mcp_servers_come_from_config_toml(tmp_path):
    graph = build_codex_installed_graph(_home(tmp_path))
    servers = {r.extra["component_path"][0]["name"] for r in _refs(graph, "mcp_server")}

    assert "user_svc" in servers


def test_claude_shaped_decoys_produce_no_nodes(tmp_path):
    """Proves the wiring dispatched to Codex's functions rather than merely
    that Codex's functions also happen to work."""
    root = _home(tmp_path)
    (root / "settings.json").write_text(
        json.dumps(
            {"enabledPlugins": {"decoy@mkt": True}, "mcpServers": {"decoy": {"url": "http://x/"}}}
        ),
        encoding="utf-8",
    )
    (root / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"decoy@mkt": [{"installPath": "/nope"}]}}),
        encoding="utf-8",
    )
    (root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"decoy2": {"url": "http://y/"}}}), encoding="utf-8"
    )
    (root / "commands").mkdir()
    (root / "commands" / "decoy.md").write_text("---\nname: decoy\n---\nX\n", encoding="utf-8")

    graph = build_codex_installed_graph(root)
    names = {n.ref.name for n in graph.nodes.values() if n.ref is not None}

    assert "decoy" not in names
    assert not any(n.kind == "command" for n in graph.nodes.values())


def test_project_layer_composes_skills_and_mcp_and_project_wins(tmp_path):
    root = _home(tmp_path)
    project = tmp_path / "proj"
    (project / ".codex" / "skills" / "proj-skill").mkdir(parents=True)
    (project / ".codex" / "skills" / "proj-skill" / "SKILL.md").write_text(
        "---\nname: proj-skill\n---\nP\n", encoding="utf-8"
    )
    (project / ".codex" / "config.toml").write_text(
        '[mcp_servers.user_svc]\ncommand = "project-wins"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root, project)
    skill_names = _skill_names(graph)
    user_svc = _server_named(graph, "user_svc")

    assert "proj-skill" in skill_names
    assert len(user_svc) == 1, "project entry replaces the user one, not both"
    assert str(project) in user_svc[0].source_manifest


def test_project_hooks_are_not_composed_at_the_endpoint(tmp_path):
    """`.codex/hooks.json` is declared-only (Task 6), never endpoint state."""
    root = _home(tmp_path)
    project = tmp_path / "proj2"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")

    graph = build_codex_installed_graph(root, project)

    assert not any(n.kind == "hook" for n in graph.nodes.values())


def test_a_malformed_first_candidate_falls_through_to_the_second(tmp_path):
    """First *qualifying* candidate wins, not first found."""
    root = _home(tmp_path)
    d = root / "plugins" / "cache" / "mkt" / "twoface" / "1.0.0"
    (d / ".codex-plugin").mkdir(parents=True)
    (d / ".codex-plugin" / "plugin.json").write_text("{ not json", encoding="utf-8")
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "twoface", "version": "1.0.0"}), encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)

    assert "twoface" in _plugins(graph)


def test_a_non_boolean_enabled_value_falls_back_to_enabled(tmp_path):
    """Same shape as claude_install's 'must be a boolean' handling."""
    root = _home(tmp_path)
    (root / "config.toml").write_text(
        CONFIG.replace(
            '[plugins."offpl@mkt"]\nenabled = false', '[plugins."offpl@mkt"]\nenabled = "yes"'
        ),
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)

    assert _plugins(graph)["offpl"].extra["enabled"] is True


def test_mcp_launch_dependencies_attach_after_the_forked_seeds(tmp_path):
    """ADR-0039. Proves `finalize_graph` ran once, AFTER Codex's own MCP seed:
    if it ran before, the server ref would not exist yet and its launch
    dependency could never resolve.

    The launched package must be resolvable to a real manifest for anything to
    attach — an `npx` spec alone resolves to nothing, which is why Claude
    Code's own endpoint golden has no package nodes either.
    """
    root = _home(tmp_path)
    project = tmp_path / "proj-deps"
    # Declared in the PROJECT config: `_attach_mcp_launch_deps` only resolves
    # against the project root for servers whose own manifest lives there,
    # matching Claude Code's behaviour for project-scoped MCPs.
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[mcp_servers.proj_svc]\ncommand = "npx"\nargs = ["@scope/tool@1.2.3"]\n',
        encoding="utf-8",
    )
    # Outside node_modules: the name index deliberately skips it.
    (project / "packages" / "tool").mkdir(parents=True)
    # The resolved manifest's own dependencies are what attach, so it must
    # declare one — resolving to a manifest with no deps attaches nothing.
    (project / "packages" / "tool" / "package.json").write_text(
        json.dumps(
            {"name": "@scope/tool", "version": "1.2.3", "dependencies": {"left-pad": "1.0.0"}}
        ),
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root, project)
    proj_svc = _server_named(graph, "proj_svc")[0]
    server_key = next(k for k, n in graph.nodes.items() if n.ref is proj_svc)
    children = [graph.nodes[e.child].kind for e in graph.edges if e.parent == server_key]

    assert "package" in children, "the launched package resolves to the project manifest"


def test_plugin_version_prefers_the_manifest_over_the_cache_segment(tmp_path):
    """The manifest is the authority; the directory is where the cache put it."""
    root = _home(tmp_path)
    d = root / "plugins" / "cache" / "mkt" / "versioned" / "9.9.9" / ".codex-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(
        json.dumps({"name": "versioned", "version": "1.2.3"}), encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)

    assert _plugins(graph)["versioned"].version == "1.2.3"


def test_a_local_cache_segment_is_not_emitted_as_a_version(tmp_path):
    """Codex names a locally-sourced bundle's directory `local`. Emitting that
    as a version asserts one the plugin does not have, and advisory matching on
    the literal string is meaningless — absent is the honest answer."""
    root = _home(tmp_path)
    d = root / "plugins" / "cache" / "mkt" / "copied" / "local" / ".codex-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({"name": "copied"}), encoding="utf-8")

    graph = build_codex_installed_graph(root)

    assert _plugins(graph)["copied"].version is None


def test_an_ordinary_version_segment_still_survives(tmp_path):
    """Guards against over-correcting: only known layout markers are dropped."""
    root = _home(tmp_path)
    d = root / "plugins" / "cache" / "mkt" / "plain" / "2.0.0" / ".codex-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({"name": "plain"}), encoding="utf-8")

    graph = build_codex_installed_graph(root)

    assert _plugins(graph)["plain"].version == "2.0.0"


def test_profile_declared_mcp_servers_are_composed(tmp_path):
    """`codex -p <name>` layers `<root>/<name>.config.toml` over the base and
    it carries the same schema — verified by running `codex -p work mcp list`
    against a fixture root and seeing the profile's server listed. Reading only
    config.toml would miss every server a profile adds."""
    root = _home(tmp_path)
    (root / "work.config.toml").write_text(
        '[mcp_servers.profile_only]\ncommand = "true"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)
    servers = {r.extra["component_path"][0]["name"] for r in _refs(graph, "mcp_server")}

    assert {"user_svc", "profile_only"} <= servers


def test_the_base_config_is_not_read_twice_as_a_profile(tmp_path):
    """`config.toml` must not match the `*.config.toml` profile glob."""
    root = _home(tmp_path)

    graph = build_codex_installed_graph(root)
    user_svc = [
        r for r in _refs(graph, "mcp_server") if r.extra["component_path"][0]["name"] == "user_svc"
    ]

    assert len(user_svc) == 1


def test_two_profiles_declaring_the_same_name_are_both_reported(tmp_path):
    """Which profile is active is an invocation flag leaving no trace on disk,
    so collapsing them by name would hide whichever lost."""
    root = _home(tmp_path)
    (root / "a.config.toml").write_text(
        '[mcp_servers.shared]\ncommand = "npx"\nargs = ["@scope/one@1.0.0"]\n', encoding="utf-8"
    )
    (root / "b.config.toml").write_text(
        '[mcp_servers.shared]\ncommand = "npx"\nargs = ["@scope/two@2.0.0"]\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)
    shared = [
        r for r in _refs(graph, "mcp_server") if r.extra["component_path"][0]["name"] == "shared"
    ]

    assert len(shared) == 2, "both profiles' servers are reachable"


def test_a_root_with_no_profiles_is_unaffected(tmp_path):
    graph = build_codex_installed_graph(_home(tmp_path))

    assert len(_refs(graph, "mcp_server")) == 1
