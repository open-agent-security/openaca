"""Tests for Cursor's declared (repo-scan) composition builder.

`docs/specs/cursor-agent-kind.md` and `.superpowers/sdd/042-cursor-agent-kind/
task-5-brief.md` are the specs these pin: dual-format plugin realization
(once, never twice), the strict-nesting exclusion for Agent Plugins fixture
content, recursive multi-root skill discovery, the `skills-cursor` denylist,
and the disjoint command/subagent extension sets.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import pytest

from tools.graph import Graph
from tools.graph_build_cursor import build_cursor_graph


@dataclass(frozen=True)
class _FakeAgent:
    """Duck-typed stand-in for `tools.agent_kinds.AgentInstance` — this
    module must not import `tools.agent_kinds` (ADR-0044's one-way
    dependency), so the test supplies the same shape directly."""

    source: Literal["declared", "installed"]
    scan_root: Optional[Path] = None
    config_root: Optional[Path] = None
    project_root: Optional[Path] = None
    bom_ref: str = "openaca:agent/cursor"
    root_label: str = "cursor"


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _write_json(path: Path, data: dict) -> Path:
    return _write(path, json.dumps(data))


def _build(repo_root: Path) -> Graph:
    graph = build_cursor_graph(_FakeAgent(source="declared", scan_root=repo_root))
    graph.validate()
    return graph


def _build_installed(config_root: Path, project_root: Optional[Path] = None) -> Graph:
    graph = build_cursor_graph(
        _FakeAgent(source="installed", config_root=config_root, project_root=project_root)
    )
    graph.validate()
    return graph


def _nodes_of_kind(graph: Graph, kind: str) -> list:
    return [n for n in graph.nodes.values() if n.kind == kind]


def test_empty_repo_yields_only_target(tmp_path):
    graph = _build(tmp_path)
    assert list(graph.nodes.values()) == [graph.root]


# --- Plugin realization -----------------------------------------------


def test_native_plugin_realizes(tmp_path):
    _write_json(
        tmp_path / "myplugin" / ".cursor-plugin" / "plugin.json",
        {"name": "myplugin", "author": {"name": "me"}},
    )

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["myplugin"]


def test_dual_format_directory_realizes_once(tmp_path):
    """Single-parent hazard: `.cursor-plugin/plugin.json` AND a root
    `plugin.json` (Agent Plugins) in the SAME directory must realize once —
    `.cursor-plugin` wins per the candidate order, and `graph.validate()`
    (called inside `_build`) would raise on a double-parented `skills/` or
    `mcp.json` if this regressed."""
    root = tmp_path / "bundle"
    _write_json(root / ".cursor-plugin" / "plugin.json", {"name": "native", "author": {}})
    _write_json(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "agent-plugins-name",
        },
    )
    _write(root / "skills" / "demo" / "SKILL.md", "---\nname: demo\n---\nbody")

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert len(plugins) == 1
    assert plugins[0].ref.name == "native"
    skills = _nodes_of_kind(graph, "skill")
    assert len(skills) == 1


def test_agent_plugins_bundle_realizes(tmp_path):
    root = tmp_path / "portable"
    _write_json(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "portable-plugin",
        },
    )
    _write(root / "skills" / "demo" / "SKILL.md", "---\nname: demo\n---\nbody")

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["portable-plugin"]
    skills = _nodes_of_kind(graph, "skill")
    assert len(skills) == 1
    skill_parent = next(e.parent for e in graph.edges if e.child == skills[0].key)
    assert skill_parent == plugins[0].key


def test_claude_plugin_bundle_with_root_dot_mcp_json_realizes_under_cursor(tmp_path):
    """Coordinator fix round: `BundledLayout.mcp_filenames` is an ordered
    tuple, not a single name — Cursor's folder discovery accepts root
    `mcp.json` OR `.mcp.json` (docs/specs/cursor-agent-kind.md:283). A
    `.claude-plugin`-identified bundle (the reused Claude Code format,
    candidate #2 in `CURSOR_SURFACE.plugin_formats`) that only carries a
    `.mcp.json` — not `mcp.json` — must still realize its bundled servers
    when scanned under `CURSOR_SURFACE`. A single-`str` field would have
    forced `mcp_filenames` to pick just one of the two, silently dropping
    this case."""
    root = tmp_path / "bundle"
    _write_json(root / ".claude-plugin" / "plugin.json", {"name": "claude-shaped"})
    _write_json(
        root / ".mcp.json",
        {"mcpServers": {"demo": {"command": "npx", "args": ["demo-server"]}}},
    )

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["claude-shaped"]
    servers = _nodes_of_kind(graph, "mcp_server")
    assert len(servers) == 1
    server_parent = next(e.parent for e in graph.edges if e.child == servers[0].key)
    assert server_parent == plugins[0].key


def test_claude_plugin_bundle_custom_skills_field_honored_under_cursor(tmp_path):
    """Coordinator fix round: a `.claude-plugin`-identified bundle (candidate
    #2 under `CURSOR_SURFACE`) declaring a custom `"skills"` field must have
    that field read from the manifest `CURSOR_SURFACE` actually resolved
    (`.claude-plugin/plugin.json`), not a hardcoded Claude-Code-only literal.
    An explicit manifest field replaces folder discovery
    (docs/specs/cursor-agent-kind.md:283-284), so the custom dir's skill must
    be realized and the default `skills/` dir must NOT be (it doesn't exist
    here)."""
    root = tmp_path / "bundle"
    _write_json(
        root / ".claude-plugin" / "plugin.json",
        {"name": "claude-shaped", "skills": "./custom-skills"},
    )
    _write(root / "custom-skills" / "demo" / "SKILL.md", "---\nname: demo\n---\nbody")

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["claude-shaped"]
    skills = _nodes_of_kind(graph, "skill")
    assert len(skills) == 1
    skill_parent = next(e.parent for e in graph.edges if e.child == skills[0].key)
    assert skill_parent == plugins[0].key


def test_native_plugin_inline_hook_source_manifest_exists_under_cursor(tmp_path):
    """Coordinator fix round: an inline `hooks` block in a NATIVE
    `.cursor-plugin/plugin.json` manifest must stamp the hook ref's
    `source_manifest` with the manifest that was actually read — this is the
    case that diverges from a hardcoded `.claude-plugin/plugin.json` literal,
    which does not exist for a native bundle. A `.claude-plugin` bundle (below)
    would pass even against the hardcoded literal, since the two paths
    coincide there; only the native format actually exercises the bug."""
    root = tmp_path / "bundle"
    manifest = root / ".cursor-plugin" / "plugin.json"
    _write_json(
        manifest,
        {
            "name": "native",
            "author": {},
            "hooks": {"PreToolUse": [{"type": "command", "command": "echo hi"}]},
        },
    )

    graph = _build(tmp_path)

    hooks = _nodes_of_kind(graph, "hook")
    assert len(hooks) == 1
    assert hooks[0].ref is not None
    source_manifest = hooks[0].ref.source_manifest
    assert source_manifest is not None
    assert Path(source_manifest).resolve() == manifest.resolve()
    assert Path(source_manifest).is_file()


def test_claude_plugin_bundle_inline_hook_source_manifest_exists_under_cursor(tmp_path):
    """Claude-Code-manifest control: a `.claude-plugin` bundle realized under
    `CURSOR_SURFACE` (candidate #2) must ALSO stamp the real manifest. This
    case coincidentally matches the old hardcoded literal, so it alone would
    not have caught the bug the native-format test above catches."""
    root = tmp_path / "bundle"
    manifest = root / ".claude-plugin" / "plugin.json"
    _write_json(
        manifest,
        {
            "name": "claude-shaped",
            "hooks": {"PreToolUse": [{"type": "command", "command": "echo hi"}]},
        },
    )

    graph = _build(tmp_path)

    hooks = _nodes_of_kind(graph, "hook")
    assert len(hooks) == 1
    assert hooks[0].ref is not None
    source_manifest = hooks[0].ref.source_manifest
    assert source_manifest is not None
    assert Path(source_manifest).resolve() == manifest.resolve()
    assert Path(source_manifest).is_file()


def test_agent_plugins_fixture_content_strictly_below_native_root_excluded(tmp_path):
    """A native `.cursor-plugin` bundle's own fixture content (e.g. a demo
    bundled INSIDE it for its own tests) must not realize as an independent
    top-level plugin — it is strictly below the already-realized native
    root."""
    root = tmp_path / "outer"
    _write_json(root / ".cursor-plugin" / "plugin.json", {"name": "outer", "author": {}})
    _write_json(
        root / "examples" / "demo" / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "demo-fixture",
        },
    )

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["outer"]


def test_agent_plugins_fixture_content_strictly_below_agent_plugins_root_excluded(tmp_path):
    """The nested-exclusion rule is format-agnostic: an Agent Plugins bundle's
    own fixture content, itself a schema-valid Agent Plugins manifest, must
    not realize as an independent top-level plugin either — regression for a
    Codex finding that the original exclusion only ever compared Agent
    Plugins candidates against realized NATIVE roots, so an Agent-Plugins-
    under-Agent-Plugins nesting fell through untouched."""
    root = tmp_path / "outer"
    _write_json(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "outer",
        },
    )
    _write_json(
        root / "examples" / "demo" / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "demo-fixture",
        },
    )

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["outer"]


def test_native_plugin_nested_under_native_root_excluded(tmp_path):
    """A native `.cursor-plugin` bundle nested strictly below another
    realized native root must not realize as an independent sibling —
    regression for a Codex finding that the original realization loop
    unconditionally realized every native candidate, regardless of
    ancestry, before the nested-exclusion check even ran."""
    root = tmp_path / "outer"
    _write_json(root / ".cursor-plugin" / "plugin.json", {"name": "outer", "author": {}})
    _write_json(
        root / "examples" / "demo" / ".cursor-plugin" / "plugin.json",
        {"name": "demo-fixture", "author": {}},
    )

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["outer"]


def test_invalid_agent_plugins_root_falls_back_to_valid_claude_plugin(tmp_path):
    """A schema-recognized but `validate_manifest`-failing root `plugin.json`
    (bad name) alongside a valid `.claude-plugin/plugin.json` in the SAME
    directory realizes the latter — not zero plugins."""
    root = tmp_path / "mixed"
    _write_json(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "Invalid Name!",
        },
    )
    _write_json(root / ".claude-plugin" / "plugin.json", {"name": "claude-fallback"})

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["claude-fallback"]


def test_invalid_agent_plugins_root_alone_records_a_warning(tmp_path):
    """A schema-recognized but `validate_manifest`-failing root `plugin.json`
    with no other plugin format present realizes zero plugins — but must not
    do so silently. `_resolve_plugin_format` already confirmed this file's
    `$schema` qualifies it as Agent Plugins, so a subsequent validation
    failure is a real defect in a manifest the scan committed to, not a
    guard miss; it must surface as a `graph.warnings` entry (an evidence
    gap), the same way a malformed `.mcp.json`/`SKILL.md` does."""
    root = tmp_path / "invalid-only"
    _write_json(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "Invalid Name!",
        },
    )

    graph = _build(tmp_path)

    assert _nodes_of_kind(graph, "plugin") == []
    assert any("plugin.json" in warning for warning in graph.warnings)


# --- Skills --------------------------------------------------------------


@pytest.mark.parametrize("config_dir", [".cursor", ".agents", ".claude", ".codex"])
def test_skill_discovered_in_each_of_four_roots(tmp_path, config_dir):
    _write(
        tmp_path / config_dir / "skills" / "demo" / "SKILL.md",
        "---\nname: demo\n---\nbody",
    )

    graph = _build(tmp_path)

    assert len(_nodes_of_kind(graph, "skill")) == 1


def test_skill_discovered_at_nested_depth_under_skills_dir(tmp_path):
    """Cursor's skill traversal is recursive (unlike Claude Code's own
    one-level project-skill walk): a `SKILL.md` several directories below
    `skills/` still realizes."""
    _write(
        tmp_path / ".cursor" / "skills" / "category" / "nested" / "demo" / "SKILL.md",
        "---\nname: demo\n---\nbody",
    )

    graph = _build(tmp_path)

    assert len(_nodes_of_kind(graph, "skill")) == 1


def test_skills_cursor_never_inventoried(tmp_path):
    _write(
        tmp_path / ".cursor" / "skills-cursor" / "vendor" / "SKILL.md",
        "---\nname: vendor\n---\nbody",
    )

    graph = _build(tmp_path)

    assert _nodes_of_kind(graph, "skill") == []


def test_skills_inside_realized_plugin_not_double_owned(tmp_path):
    """A plugin root is a boundary handoff: it owns its ENTIRE subtree, even
    a `.cursor/skills/` dir inside it that isn't part of its own bundled
    contract — the target-level Cursor skill walk must not also claim it
    (single-parent)."""
    root = tmp_path / "plug"
    _write_json(root / ".cursor-plugin" / "plugin.json", {"name": "plug", "author": {}})
    _write(root / "skills" / "bundled" / "SKILL.md", "---\nname: bundled\n---\nbody")
    _write(
        root / ".cursor" / "skills" / "nested" / "SKILL.md",
        "---\nname: nested\n---\nbody",
    )

    graph = _build(tmp_path)
    skills = _nodes_of_kind(graph, "skill")
    assert len(skills) == 1
    plugin_key = _nodes_of_kind(graph, "plugin")[0].key
    parent = next(e.parent for e in graph.edges if e.child == skills[0].key)
    assert parent == plugin_key


# --- MCP -------------------------------------------------------------


def test_scoped_cursor_mcp_json_realizes(tmp_path):
    _write_json(
        tmp_path / ".cursor" / "mcp.json",
        {"mcpServers": {"demo": {"command": "npx", "args": ["demo-server"]}}},
    )

    graph = _build(tmp_path)

    servers = _nodes_of_kind(graph, "mcp_server")
    assert len(servers) == 1
    assert servers[0].ref.source_locator == "$.mcpServers.demo"


def test_bare_mcp_json_not_matched_by_scoped_surface(tmp_path):
    """Cursor's declared MCP surface is scoped (`.cursor/mcp.json`), not an
    any-name pattern — a bare top-level `mcp.json` is not a Cursor MCP
    surface."""
    _write_json(
        tmp_path / "mcp.json",
        {"mcpServers": {"demo": {"command": "npx", "args": ["demo-server"]}}},
    )

    graph = _build(tmp_path)

    assert _nodes_of_kind(graph, "mcp_server") == []


# --- Commands and subagents -------------------------------------------


def test_command_accepts_txt_not_mdc(tmp_path):
    _write(tmp_path / ".cursor" / "commands" / "deploy.txt", "deploy body")
    _write(tmp_path / ".cursor" / "commands" / "ignored.mdc", "ignored body")

    graph = _build(tmp_path)

    commands = _nodes_of_kind(graph, "command")
    assert [c.ref.source_manifest.endswith("deploy.txt") for c in commands] == [True]


def test_subagent_accepts_mdc_not_txt(tmp_path):
    _write(tmp_path / ".cursor" / "agents" / "reviewer.mdc", "reviewer body")
    _write(tmp_path / ".cursor" / "agents" / "ignored.txt", "ignored body")

    graph = _build(tmp_path)

    agents = _nodes_of_kind(graph, "agent")
    assert [a.ref.source_manifest.endswith("reviewer.mdc") for a in agents] == [True]


def test_command_and_subagent_precedence_routed_through_task4_resolvers(tmp_path):
    """`.cursor` beats `.claude` for a same-relative-path subagent (Task 4
    first-wins); confirms the graph builder routes through the resolver
    rather than walking the directories itself."""
    _write(tmp_path / ".cursor" / "agents" / "deploy.md", "cursor version")
    _write(tmp_path / ".claude" / "agents" / "deploy.md", "claude version")

    graph = _build(tmp_path)

    agents = _nodes_of_kind(graph, "agent")
    assert len(agents) == 1
    assert agents[0].ref.source_manifest.endswith(str(Path(".cursor/agents/deploy.md")))


def test_command_traversal_depth_10_included_depth_11_excluded(tmp_path):
    commands_dir = tmp_path / ".cursor" / "commands"
    _write(commands_dir.joinpath(*["d"] * 9, "at-depth-10.md"), "included")
    _write(commands_dir.joinpath(*["d"] * 10, "at-depth-11.md"), "excluded")

    graph = _build(tmp_path)

    commands = _nodes_of_kind(graph, "command")
    manifests = {c.ref.source_manifest for c in commands}
    assert any(m.endswith("at-depth-10.md") for m in manifests)
    assert not any(m.endswith("at-depth-11.md") for m in manifests)


def test_gitignored_command_and_subagent_excluded_by_default(tmp_path):
    """Neither `cursor_commands.resolve_repo` nor `cursor_subagents.resolve_repo`
    is gitignore-aware itself (both walk with unrestricted `Path.rglob`), so
    the graph builder must filter their resolved paths before emission —
    parity with `_add_cursor_skills`/`_add_scoped_mcps` in the same module."""
    _write(tmp_path / ".gitignore", "ignored/\n")
    _write(tmp_path / "ignored" / ".cursor" / "commands" / "deploy.md", "deploy body")
    _write(tmp_path / "ignored" / ".cursor" / "agents" / "reviewer.md", "reviewer body")
    _write(tmp_path / ".cursor" / "commands" / "kept.md", "kept body")

    graph = _build(tmp_path)

    commands = _nodes_of_kind(graph, "command")
    agents = _nodes_of_kind(graph, "agent")
    assert [c.ref.source_manifest.endswith("kept.md") for c in commands] == [True]
    assert agents == []

    included_graph = build_cursor_graph(
        _FakeAgent(source="declared", scan_root=tmp_path), include_gitignored=True
    )
    included_graph.validate()
    included_commands = _nodes_of_kind(included_graph, "command")
    included_agents = _nodes_of_kind(included_graph, "agent")
    assert len(included_commands) == 2
    assert len(included_agents) == 1


def test_ignored_higher_precedence_command_and_subagent_fall_back_to_lower_precedence(tmp_path):
    """A gitignored higher-precedence file must not shadow an unignored
    lower-precedence file at the same relative path.

    Commands are last-wins with `.cursor` overwriting `.claude` in the same
    scope, and subagents are first-wins with `.cursor` beating `.claude` — so
    in both resolvers `.cursor/.../deploy.md` normally wins over
    `.claude/.../deploy.md`. Filtering only the resolution winner (rather
    than filtering before resolution) would drop the ignored `.cursor` entry
    from the graph WITHOUT recovering the `.claude` entry it had already
    displaced, losing the command/subagent entirely instead of falling back."""
    _write(tmp_path / ".gitignore", ".cursor/commands/deploy.md\n.cursor/agents/deploy.md\n")
    _write(tmp_path / ".cursor" / "commands" / "deploy.md", "cursor version")
    _write(tmp_path / ".claude" / "commands" / "deploy.md", "claude version")
    _write(tmp_path / ".cursor" / "agents" / "deploy.md", "cursor version")
    _write(tmp_path / ".claude" / "agents" / "deploy.md", "claude version")

    graph = _build(tmp_path)

    commands = _nodes_of_kind(graph, "command")
    agents = _nodes_of_kind(graph, "agent")
    assert len(commands) == 1
    assert commands[0].ref.source_manifest.endswith(str(Path(".claude/commands/deploy.md")))
    assert len(agents) == 1
    assert agents[0].ref.source_manifest.endswith(str(Path(".claude/agents/deploy.md")))


def test_gitignored_higher_precedence_plugin_manifest_falls_back_to_lower_precedence(tmp_path):
    """A gitignored higher-precedence plugin manifest must not win format
    resolution over an unignored lower-precedence one in the SAME directory.

    `.cursor-plugin/plugin.json` (candidate #1) and `.claude-plugin/plugin.json`
    (candidate #2) both qualify here; `_resolve_plugin_format` used to check
    only `manifest.is_file()`, so an ignored `.cursor-plugin/plugin.json`
    would still win and the plugin would realize using content a default
    scan (no `--include-gitignored`) was supposed to exclude."""
    root = tmp_path / "bundle"
    _write(tmp_path / ".gitignore", "bundle/.cursor-plugin/\n")
    _write_json(root / ".cursor-plugin" / "plugin.json", {"name": "ignored-native", "author": {}})
    _write_json(root / ".claude-plugin" / "plugin.json", {"name": "visible-claude-shaped"})

    graph = _build(tmp_path)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["visible-claude-shaped"]

    included_graph = build_cursor_graph(
        _FakeAgent(source="declared", scan_root=tmp_path), include_gitignored=True
    )
    included_graph.validate()
    included_plugins = _nodes_of_kind(included_graph, "plugin")
    assert [p.ref.name for p in included_plugins] == ["ignored-native"]


def test_gitignored_bundled_mcp_filename_falls_back_to_unignored_one(tmp_path):
    """A gitignored higher-precedence bundled MCP filename must not win the
    plugin's folder-discovery ordered-candidate selection over an unignored
    lower-precedence one in the SAME plugin root.

    `CURSOR_SURFACE.bundled.mcp_filenames` tries `mcp.json` before `.mcp.json`.
    `_parse_default_mcp` used to pick the first EXISTING candidate regardless
    of gitignore; the later gitignore filter on emitted refs then dropped the
    ignored `mcp.json`'s servers without ever trying `.mcp.json`, losing the
    bundled MCP server entirely instead of falling back to the unignored
    file."""
    root = tmp_path / "bundle"
    _write(tmp_path / ".gitignore", "bundle/mcp.json\n")
    _write_json(root / ".cursor-plugin" / "plugin.json", {"name": "native", "author": {}})
    _write_json(
        root / "mcp.json",
        {"mcpServers": {"ignored-demo": {"command": "npx", "args": ["ignored"]}}},
    )
    _write_json(
        root / ".mcp.json",
        {"mcpServers": {"visible-demo": {"command": "npx", "args": ["visible"]}}},
    )

    graph = _build(tmp_path)

    servers = _nodes_of_kind(graph, "mcp_server")
    assert len(servers) == 1
    assert Path(servers[0].ref.source_manifest).name == ".mcp.json"

    included_graph = build_cursor_graph(
        _FakeAgent(source="declared", scan_root=tmp_path), include_gitignored=True
    )
    included_graph.validate()
    included_servers = _nodes_of_kind(included_graph, "mcp_server")
    assert len(included_servers) == 1
    assert Path(included_servers[0].ref.source_manifest).name == "mcp.json"


# --- Dependency manifests -----------------------------------------------


def test_scan_root_dep_manifest_attached_to_target(tmp_path):
    _write_json(
        tmp_path / "package.json", {"name": "root-pkg", "dependencies": {"left-pad": "1.0.0"}}
    )

    graph = _build(tmp_path)

    packages = _nodes_of_kind(graph, "package")
    assert any(p.ref.name == "left-pad" for p in packages)


# --- Installed (endpoint) composition ------------------------------------
#
# Test-isolation hazard: home-scoped compat roots (`~/.agents`, `~/.claude`,
# `~/.codex`) are resolved from `Path.home()`, never from `config_root` — so
# every test in this section monkeypatches `Path.home` to a fixture-owned
# directory DISTINCT from `config_root`, or it would read the developer's
# real `~/.claude`/`~/.agents`.


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "fakehome"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


def test_installed_dev_linked_plugin_realizes(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    _write_json(
        config_root / "plugins" / "local" / "mydev" / ".cursor-plugin" / "plugin.json",
        {"name": "mydev", "author": {}},
    )

    graph = _build_installed(config_root)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["mydev"]


def test_installed_cached_bundle_with_sentinel_realizes(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    sha_dir = config_root / "plugins" / "cache" / "acme-market" / "widget" / "abc123"
    _write_json(sha_dir / ".cursor-plugin" / "plugin.json", {"name": "widget", "author": {}})
    _write(sha_dir / ".cache-complete")

    graph = _build_installed(config_root)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["widget"]
    assert plugins[0].ref.extra.get("cursor_marketplace_dir") == "acme-market"
    # ADR-0052: the recorded marketplace segment IS the qualifying key, so a
    # marketplace-installed bundle takes `plugin/{marketplace}/{name}` and every
    # component bundled inside it inherits a plugin-private identity.
    assert plugins[0].ref.extra.get("marketplace") == "acme-market"
    assert plugins[0].ref.component_identity == "plugin/acme-market/widget"


def test_installed_cached_agent_plugins_bundle_has_no_duplicate_self_ref(tmp_path, fake_home):
    """A marketplace-cached Agent Plugins bundle stamps `plugin_extra` onto a
    NEW `self_ref` object (`dataclasses.replace`); the identity check that
    skips the self ref while walking `refs` must compare against the
    original object still sitting in that list, or the pre-extra ref falls
    through the loop and re-emits as a second, marketplace-less nested
    plugin — inflating active-plugin counts in endpoint BOMs."""
    config_root = tmp_path / "cursor_config"
    sha_dir = config_root / "plugins" / "cache" / "acme-market" / "portable" / "sha1"
    _write_json(
        sha_dir / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "portable-plugin",
        },
    )
    _write(sha_dir / ".cache-complete")

    graph = _build_installed(config_root)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["portable-plugin"]
    assert plugins[0].ref.component_identity == "plugin/acme-market/portable-plugin"


def test_installed_dev_linked_bundle_gets_no_marketplace_identity(tmp_path, fake_home):
    """A `plugins/local/` bundle is not registry-resolved: its directory name is
    chosen by whoever made the symlink, so minting cross-BOM identity from it is
    the hole ADR-0052 closes. Occurrence-local, like a repo-declared plugin."""
    local = fake_home / ".cursor" / "plugins" / "local" / "devplug"
    _write(local / ".cursor-plugin" / "plugin.json", '{"name": "devplug"}')

    graph = _build_installed(fake_home / ".cursor")

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["devplug"]
    assert "marketplace" not in plugins[0].ref.extra
    assert plugins[0].ref.component_identity is None


def test_installed_cached_bundle_missing_sentinel_not_inventoried(tmp_path, fake_home):
    """A cache directory without `.cache-complete` is a cache miss Cursor
    reinstalls rather than loads — not inventoried at all, while a complete
    sibling bundle is."""
    config_root = tmp_path / "cursor_config"
    incomplete = config_root / "plugins" / "cache" / "acme-market" / "incomplete" / "sha1"
    _write_json(incomplete / ".cursor-plugin" / "plugin.json", {"name": "incomplete", "author": {}})
    complete = config_root / "plugins" / "cache" / "acme-market" / "complete" / "sha2"
    _write_json(complete / ".cursor-plugin" / "plugin.json", {"name": "complete", "author": {}})
    _write(complete / ".cache-complete")

    graph = _build_installed(config_root)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["complete"]


def test_installed_manifest_less_complete_bundle_synthesizes_presence_only_ref(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    sha_dir = config_root / "plugins" / "cache" / "acme-market" / "bare" / "sha3"
    _write(sha_dir / "skills" / "demo" / "SKILL.md", "---\nname: demo\n---\nbody")
    _write(sha_dir / "commands" / "deploy.md", "deploy body")
    _write(sha_dir / ".cache-complete")

    graph = _build_installed(config_root)

    plugins = _nodes_of_kind(graph, "plugin")
    assert [p.ref.name for p in plugins] == ["bare"]
    assert plugins[0].ref.extra.get("manifest") == "absent"
    assert "enabled" not in plugins[0].ref.extra
    assert "active" not in plugins[0].ref.extra
    children = [graph.nodes[e.child] for e in graph.edges if e.parent == plugins[0].key]
    assert {c.kind for c in children} == {"skill", "command"}


def test_installed_no_plugin_ever_carries_enabled(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    _write_json(
        config_root / "plugins" / "local" / "mydev" / ".cursor-plugin" / "plugin.json",
        {"name": "mydev", "author": {}},
    )

    graph = _build_installed(config_root)

    for plugin in _nodes_of_kind(graph, "plugin"):
        assert "enabled" not in (plugin.ref.extra or {})
        assert "active" not in (plugin.ref.extra or {})


def test_installed_home_scoped_claude_agents_keys_by_label_not_absolute_path(tmp_path, fake_home):
    """`~/.claude/agents/x.md` must key as `claude-code/agents/x.md#...`, not
    a machine-specific absolute path — proving home-scoped roots resolve
    through `Path.home()` (via `fake_home`) rather than through
    `config_root`, which here lives in a completely separate directory."""
    config_root = tmp_path / "cursor_config"
    _write(fake_home / ".claude" / "agents" / "x.md", "agent body")

    graph = _build_installed(config_root)

    agents = _nodes_of_kind(graph, "agent")
    assert len(agents) == 1
    assert agents[0].key.startswith("claude-code/agents/x.md#")


def test_installed_config_dir_relocation_does_not_move_home_scoped_roots(tmp_path, fake_home):
    """Rule 4: `--config-dir` relocates Cursor's own root, never the
    cross-tool compat roots. `config_root` here is deliberately NOT under
    `fake_home`, and the `~/.agents/skills` fixture is still found."""
    config_root = tmp_path / "somewhere-else" / "relocated-cursor"
    _write(fake_home / ".agents" / "skills" / "demo" / "SKILL.md", "---\nname: demo\n---\nbody")

    graph = _build_installed(config_root)

    assert len(_nodes_of_kind(graph, "skill")) == 1


def test_installed_mcp_merge_project_wins_same_name(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    project_root = tmp_path / "project"
    _write_json(
        config_root / "mcp.json",
        {"mcpServers": {"shared": {"command": "npx", "args": ["user-version"]}}},
    )
    _write_json(
        project_root / ".cursor" / "mcp.json",
        {"mcpServers": {"shared": {"command": "npx", "args": ["project-version"]}}},
    )

    graph = _build_installed(config_root, project_root)

    servers = _nodes_of_kind(graph, "mcp_server")
    assert len(servers) == 1
    assert servers[0].ref.source_manifest == str(project_root / ".cursor" / "mcp.json")


def test_installed_mcp_merge_unique_entries_from_both_files_survive(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    project_root = tmp_path / "project"
    _write_json(
        config_root / "mcp.json",
        {"mcpServers": {"user-only": {"command": "npx", "args": ["a"]}}},
    )
    _write_json(
        project_root / ".cursor" / "mcp.json",
        {"mcpServers": {"project-only": {"command": "npx", "args": ["b"]}}},
    )

    graph = _build_installed(config_root, project_root)

    servers = _nodes_of_kind(graph, "mcp_server")
    assert {s.ref.name for s in servers} == {"a", "b"}


def test_installed_mcp_merge_malformed_project_file_does_not_drop_user_entries(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    project_root = tmp_path / "project"
    _write_json(
        config_root / "mcp.json",
        {"mcpServers": {"user-only": {"command": "npx", "args": ["a"]}}},
    )
    _write(project_root / ".cursor" / "mcp.json", "{not valid json")

    graph = _build_installed(config_root, project_root)

    servers = _nodes_of_kind(graph, "mcp_server")
    assert {s.ref.name for s in servers} == {"a"}


def test_installed_commands_and_subagents_via_task4_resolvers(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    project_root = tmp_path / "project"
    _write(project_root / ".cursor" / "agents" / "deploy.md", "project cursor version")
    _write(project_root / ".claude" / "agents" / "deploy.md", "project claude version")
    _write(config_root / "commands" / "release.txt", "release body")

    graph = _build_installed(config_root, project_root)

    agents = _nodes_of_kind(graph, "agent")
    assert len(agents) == 1
    assert agents[0].ref.source_manifest.endswith(str(Path(".cursor/agents/deploy.md")))
    assert agents[0].key.startswith("project/.cursor/agents/deploy.md#")
    commands = _nodes_of_kind(graph, "command")
    assert [c.ref.source_manifest.endswith("release.txt") for c in commands] == [True]


@pytest.mark.parametrize("project_dir", [".cursor", ".agents", ".claude", ".codex"])
def test_installed_skill_discovered_in_each_of_four_project_roots(
    tmp_path, fake_home, project_dir
):
    """`--project` must scan the same four skill roots under the project
    folder as it does under the user config root — a project-scoped skill
    was previously invisible to an installed-mode scan."""
    config_root = tmp_path / "cursor_config"
    project_root = tmp_path / "project"
    _write(
        project_root / project_dir / "skills" / "demo" / "SKILL.md",
        "---\nname: demo\n---\nbody",
    )

    graph = _build_installed(config_root, project_root)

    skills = _nodes_of_kind(graph, "skill")
    assert len(skills) == 1
    assert skills[0].key.startswith("project/")


def test_installed_skill_found_in_both_project_and_user_roots(tmp_path, fake_home):
    config_root = tmp_path / "cursor_config"
    project_root = tmp_path / "project"
    _write(
        project_root / ".cursor" / "skills" / "proj-skill" / "SKILL.md",
        "---\nname: proj-skill\n---\nbody",
    )
    _write(
        config_root / "skills" / "user-skill" / "SKILL.md",
        "---\nname: user-skill\n---\nbody",
    )

    graph = _build_installed(config_root, project_root)

    assert len(_nodes_of_kind(graph, "skill")) == 2


def test_installed_plugin_command_shadowed_by_personal_command_is_pruned(tmp_path, fake_home):
    """Regression for a Codex review finding: Commands' last-wins precedence
    (docs/specs/cursor-agent-kind.md "Precedence": team -> global -> plugin ->
    workspace -> personal) places `plugin` as its own tier. A plugin's
    bundled command must not permanently survive in the composed graph when a
    same-relative-path personal command wins — and the winning personal
    command must not ALSO appear as a second, root-parented node for the same
    occurrence the plugin descent already emitted."""
    config_root = tmp_path / "cursor_config"
    _write_json(
        config_root / "plugins" / "local" / "mydev" / ".cursor-plugin" / "plugin.json",
        {"name": "mydev", "author": {}},
    )
    _write(config_root / "plugins" / "local" / "mydev" / "commands" / "deploy.md", "plugin version")
    _write(config_root / "commands" / "deploy.md", "personal version")

    graph = _build_installed(config_root)

    commands = _nodes_of_kind(graph, "command")
    assert len(commands) == 1
    assert commands[0].ref.source_manifest.endswith(str(Path("cursor_config/commands/deploy.md")))
    plugins = _nodes_of_kind(graph, "plugin")
    assert len(plugins) == 1
    assert graph.children_of(plugins[0]) == []


def test_installed_plugin_command_without_shadow_stays_under_plugin_node(tmp_path, fake_home):
    """The other half of the precedence fix: a plugin command with no
    same-relative-path override elsewhere must still realize exactly once,
    as a child of its plugin node — the fix must not over-prune."""
    config_root = tmp_path / "cursor_config"
    _write_json(
        config_root / "plugins" / "local" / "mydev" / ".cursor-plugin" / "plugin.json",
        {"name": "mydev", "author": {}},
    )
    _write(config_root / "plugins" / "local" / "mydev" / "commands" / "deploy.md", "plugin version")

    graph = _build_installed(config_root)

    commands = _nodes_of_kind(graph, "command")
    assert len(commands) == 1
    plugins = _nodes_of_kind(graph, "plugin")
    assert len(plugins) == 1
    assert [c.key for c in graph.children_of(plugins[0])] == [commands[0].key]
