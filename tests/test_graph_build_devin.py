"""Devin CLI's composition builder (ADR-0070, docs/specs/devin-cli-agent-kind.md).

Every surface in the spec's "Where each surface loads from" table, the
`read_config_from` gate on each import, Devin's own MCP layer merge, the
record-both rule for an imported server beside a same-named native one, and
installed-mode node keys that carry no home path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from tools.graph import Graph
from tools.graph_build_devin import build_devin_declared_graph, build_devin_installed_graph


@dataclass(frozen=True)
class _FakeAgent:
    """Duck-typed `AgentInstance`: the builder must not import
    `tools.agent_kinds` (ADR-0044's one-way dependency)."""

    source: Literal["declared", "installed"]
    scan_root: Optional[Path] = None
    config_root: Optional[Path] = None
    project_root: Optional[Path] = None
    bom_ref: str = "root/devin-cli"
    root_label: str = "devin-cli"


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_json(path: Path, data: object) -> Path:
    return _write(path, json.dumps(data))


def _skill(directory: Path, name: str, extra: str = "") -> Path:
    return _write(directory / "SKILL.md", f"---\nname: {name}\n{extra}---\nDo it.\n")


def _server(command: str = "npx") -> dict:
    return {"command": command, "args": ["-y", "pkg-mcp@1.0.0"]}


def _declared(scan_root: Path, *, include_gitignored: bool = False) -> Graph:
    warnings: list[str] = []
    graph = build_devin_declared_graph(
        _FakeAgent(source="declared", scan_root=scan_root),
        include_gitignored=include_gitignored,
        warnings=warnings,
    )
    graph.validate()
    return graph


def _installed(tmp_path: Path, project: Optional[Path] = None) -> Graph:
    home = tmp_path / "home"
    graph = build_devin_installed_graph(
        _FakeAgent(
            source="installed", config_root=home / ".config" / "devin", project_root=project
        ),
        data_root=home / ".local" / "share" / "devin" / "cli",
        legacy_config_root=home / ".config" / "cognition",
        home=home,
    )
    graph.validate()
    return graph


def _refs(graph: Graph, kind: str) -> list:
    return [n.ref for n in graph.nodes.values() if n.kind == kind and n.ref is not None]


def _names(graph: Graph, kind: str) -> list[str]:
    return sorted(r.name or r.component_identity for r in _refs(graph, kind))


def _mcp(graph: Graph) -> list[tuple[str, str]]:
    """(server name, source manifest basename-with-parent) per MCP occurrence."""
    out = []
    for ref in _refs(graph, "mcp_server"):
        name = ref.extra["component_path"][-1]["name"]
        manifest = Path(ref.source_manifest)
        out.append((name, f"{manifest.parent.name}/{manifest.name}"))
    return sorted(out)


# --- Declared: MCP servers ---------------------------------------------------


def test_empty_repo_yields_only_the_root(tmp_path):
    graph = _declared(tmp_path)
    assert list(graph.nodes.values()) == [graph.root]


def test_project_mcp_config_is_read_as_json_with_comments(tmp_path):
    _write(
        tmp_path / ".devin" / "mcp_config.json",
        '{\n  // shared servers\n  "mcpServers": {"github": {"command": "npx", '
        '"args": ["-y", "@modelcontextprotocol/server-github"],},},\n}\n',
    )

    assert _mcp(_declared(tmp_path)) == [("github", ".devin/mcp_config.json")]


def test_project_local_layer_overrides_the_project_layer_by_name(tmp_path):
    _write_json(
        tmp_path / ".devin" / "mcp_config.json",
        {"mcpServers": {"github": _server("npx"), "linear": _server("npx")}},
    )
    _write_json(
        tmp_path / ".devin" / "mcp_config.local.json",
        {"mcpServers": {"github": {"command": "uvx", "args": ["github-mcp==2.0.0"]}}},
    )

    assert _mcp(_declared(tmp_path)) == [
        ("github", ".devin/mcp_config.local.json"),
        ("linear", ".devin/mcp_config.json"),
    ]


def test_a_disabled_higher_layer_entry_shadows_the_lower_definition(tmp_path):
    _write_json(tmp_path / ".devin" / "mcp_config.json", {"mcpServers": {"github": _server()}})
    _write_json(
        tmp_path / ".devin" / "mcp_config.local.json",
        {"mcpServers": {"github": {**_server(), "disabled": True}}},
    )

    assert _mcp(_declared(tmp_path)) == []


def test_legacy_mcp_servers_in_config_json_are_composed(tmp_path):
    """Releases before 3000.3 kept servers under `config.json`'s `mcpServers`;
    their order against `mcp_config.json` at the same level is unverified, so
    both occurrences are recorded."""
    _write_json(tmp_path / ".devin" / "config.json", {"mcpServers": {"github": _server()}})
    _write_json(tmp_path / ".devin" / "mcp_config.json", {"mcpServers": {"github": _server()}})

    assert _mcp(_declared(tmp_path)) == [
        ("github", ".devin/config.json"),
        ("github", ".devin/mcp_config.json"),
    ]


def test_an_imported_server_and_a_same_named_native_one_are_both_recorded(tmp_path):
    _write_json(tmp_path / ".devin" / "mcp_config.json", {"mcpServers": {"github": _server()}})
    _write_json(tmp_path / ".mcp.json", {"mcpServers": {"github": _server()}})

    refs = _refs(_declared(tmp_path), "mcp_server")

    assert sorted(Path(r.source_manifest).name for r in refs) == [".mcp.json", "mcp_config.json"]


def test_claude_code_and_cursor_mcp_files_are_imported(tmp_path):
    _write_json(tmp_path / ".mcp.json", {"flat": _server()})
    _write_json(tmp_path / ".claude" / "settings.json", {"mcpServers": {"settings": _server()}})
    _write_json(tmp_path / ".claude" / "settings.local.json", {"mcpServers": {"local": _server()}})
    _write_json(tmp_path / ".cursor" / "mcp.json", {"mcpServers": {"cursor": _server()}})

    assert _mcp(_declared(tmp_path)) == [
        ("cursor", ".cursor/mcp.json"),
        ("flat", f"{tmp_path.name}/.mcp.json"),
        ("local", ".claude/settings.local.json"),
        ("settings", ".claude/settings.json"),
    ]


def test_devin_server_config_aliases_are_honoured(tmp_path):
    """`serverUrl` is Devin's alias for `url`, and `transport` for `type`."""
    _write_json(
        tmp_path / ".devin" / "mcp_config.json",
        {
            "mcpServers": {
                "remote": {"serverUrl": "https://mcp.example.test/sse", "transport": "sse"}
            }
        },
    )

    (ref,) = _refs(_declared(tmp_path), "mcp_server")

    assert ref.extra["url"] == "https://mcp.example.test/sse"
    assert ref.extra["transport"] == "sse"


def test_a_malformed_devin_file_is_a_gap_and_does_not_hide_others(tmp_path):
    _write(tmp_path / ".devin" / "mcp_config.json", "{not json")
    _write_json(tmp_path / ".cursor" / "mcp.json", {"mcpServers": {"cursor": _server()}})

    graph = _declared(tmp_path)

    assert _mcp(graph) == [("cursor", ".cursor/mcp.json")]
    assert graph.warnings.gaps


def test_a_malformed_server_entry_drops_only_itself(tmp_path):
    _write_json(
        tmp_path / ".devin" / "mcp_config.json",
        {"mcpServers": {"broken": {"args": ["no-command"]}, "ok": _server()}},
    )

    graph = _declared(tmp_path)

    assert _mcp(graph) == [("ok", ".devin/mcp_config.json")]
    assert graph.warnings.gaps


# --- Declared: read_config_from gates every import ----------------------------


def _all_claude_imports(root: Path) -> None:
    _write_json(root / ".mcp.json", {"mcpServers": {"dotmcp": _server()}})
    _write_json(
        root / ".claude" / "settings.json",
        {
            "mcpServers": {"settings": _server()},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]},
        },
    )
    _skill(root / ".claude" / "skills" / "claude-skill", "claude-skill")
    _write(root / ".claude" / "commands" / "deploy.md", "Deploy.\n")


def test_claude_code_imports_compose_by_default(tmp_path):
    _all_claude_imports(tmp_path)

    graph = _declared(tmp_path)

    assert {name for name, _ in _mcp(graph)} == {"dotmcp", "settings"}
    assert _names(graph, "skill") == ["claude-skill"]
    assert _names(graph, "command") == ["deploy"]
    assert len(_refs(graph, "hook")) == 1


def test_a_project_switch_turns_every_claude_code_import_off(tmp_path):
    _all_claude_imports(tmp_path)
    _write(tmp_path / ".devin" / "config.json", '{"read_config_from": {"claude": false}}')

    graph = _declared(tmp_path)

    assert _mcp(graph) == []
    assert _refs(graph, "skill") == []
    assert _refs(graph, "command") == []
    assert _refs(graph, "hook") == []


def test_a_project_local_switch_wins_over_the_project_one(tmp_path):
    _all_claude_imports(tmp_path)
    _write_json(tmp_path / ".devin" / "config.json", {"read_config_from": {"claude": False}})
    _write_json(tmp_path / ".devin" / "config.local.json", {"read_config_from": {"claude": None}})

    assert _names(_declared(tmp_path), "skill") == ["claude-skill"]


def test_the_cursor_switch_gates_only_the_cursor_import(tmp_path):
    _write_json(tmp_path / ".cursor" / "mcp.json", {"mcpServers": {"cursor": _server()}})
    _write_json(tmp_path / ".mcp.json", {"mcpServers": {"dotmcp": _server()}})
    _write_json(tmp_path / ".devin" / "config.json", {"read_config_from": {"cursor": False}})

    assert {name for name, _ in _mcp(_declared(tmp_path))} == {"dotmcp"}


def test_the_nearest_project_config_decides_a_nested_import(tmp_path):
    """Nested `.devin/` configs take precedence over ancestor ones, and each
    import is judged from the directory that holds it."""
    _write_json(tmp_path / ".devin" / "config.json", {"read_config_from": {"claude": False}})
    _skill(tmp_path / ".claude" / "skills" / "root-skill", "root-skill")
    _write_json(tmp_path / "pkg" / ".devin" / "config.json", {"read_config_from": {"claude": True}})
    _skill(tmp_path / "pkg" / ".claude" / "skills" / "pkg-skill", "pkg-skill")
    _skill(tmp_path / "other" / ".claude" / "skills" / "other-skill", "other-skill")

    assert _names(_declared(tmp_path), "skill") == ["pkg-skill"]


def test_windsurf_and_copilot_switches_gate_their_skill_roots(tmp_path):
    _skill(tmp_path / ".windsurf" / "skills" / "ws", "ws")
    _skill(tmp_path / ".github" / "skills" / "gh", "gh")
    assert _names(_declared(tmp_path), "skill") == ["gh", "ws"]

    _write_json(
        tmp_path / ".devin" / "config.json",
        {"read_config_from": {"windsurf": False, "copilot": False}},
    )
    assert _names(_declared(tmp_path), "skill") == []


# --- Declared: skills, commands, subagents, hooks ------------------------------


def test_native_skill_roots_are_one_level_deep(tmp_path):
    _skill(tmp_path / ".devin" / "skills" / "devin-skill", "devin-skill")
    _skill(tmp_path / ".cognition" / "skills" / "legacy-skill", "legacy-skill")
    _skill(tmp_path / ".agents" / "skills" / "shared-skill", "shared-skill")
    _skill(tmp_path / ".devin" / "skills" / "group" / "nested", "nested")

    assert _names(_declared(tmp_path), "skill") == ["devin-skill", "legacy-skill", "shared-skill"]


def test_claude_and_copilot_skill_imports_are_recursive(tmp_path):
    _skill(tmp_path / ".claude" / "skills" / "group" / "deep", "deep")
    _skill(tmp_path / ".github" / "skills" / "team" / "tool", "tool")
    # A `SKILL.md` directly in `skills/` names no skill directory.
    _skill(tmp_path / ".claude" / "skills", "loose")

    assert _names(_declared(tmp_path), "skill") == ["deep", "tool"]


def test_skill_roots_at_any_depth_compose(tmp_path):
    """Devin walks from the working directory up, so a nested package's
    `.devin/skills` loads when Devin runs there."""
    _skill(tmp_path / "pkg" / ".devin" / "skills" / "pkg-skill", "pkg-skill")

    assert _names(_declared(tmp_path), "skill") == ["pkg-skill"]


def test_claude_code_commands_keep_the_command_type(tmp_path):
    _write(tmp_path / ".claude" / "commands" / "ops" / "deploy.md", "Deploy.\n")

    (ref,) = _refs(_declared(tmp_path), "command")

    assert ref.extra["component_type"] == "command"
    assert ref.name == "deploy"


def test_a_skill_named_file_under_commands_is_still_a_command(tmp_path):
    """`.claude/commands/**/*.md` takes every markdown file, whatever its name."""
    _write(tmp_path / ".claude" / "commands" / "release" / "SKILL.md", "Release.\n")

    graph = _declared(tmp_path)

    assert _refs(graph, "skill") == []
    assert [Path(r.source_manifest).name for r in _refs(graph, "command")] == ["SKILL.md"]


def test_subagents_in_both_project_roots_and_both_layouts(tmp_path):
    _write(tmp_path / ".devin" / "agents" / "reviewer.md", "---\ndescription: r\n---\nReview.\n")
    _write(tmp_path / ".devin" / "agents" / "researcher" / "AGENT.md", "Research.\n")
    _write(tmp_path / ".agents" / "agents" / "planner" / "agents.md", "Plan.\n")
    _write(tmp_path / ".agents" / "agents" / "notes.txt", "not an agent\n")

    assert _names(_declared(tmp_path), "agent") == ["planner", "researcher", "reviewer"]


def test_a_directory_subagent_reads_only_its_first_file(tmp_path):
    """`AGENT.md`, then `AGENTS.md`, `agent.md`, `agents.md`: one profile."""
    _write(tmp_path / ".devin" / "agents" / "researcher" / "AGENTS.md", "Second.\n")
    _write(tmp_path / ".devin" / "agents" / "researcher" / "agents.md", "Fourth.\n")

    (ref,) = _refs(_declared(tmp_path), "agent")

    assert Path(ref.source_manifest).name == "AGENTS.md"


def test_subagent_frontmatter_declares_no_components_for_devin(tmp_path):
    """Devin documents no `mcpServers` or `hooks` subagent field, so a
    Claude-shaped frontmatter block composes only the subagent itself."""
    _write(
        tmp_path / ".devin" / "agents" / "reviewer.md",
        "---\nmcpServers:\n  inline:\n    command: npx\n---\nReview.\n",
    )

    graph = _declared(tmp_path)

    assert _names(graph, "agent") == ["reviewer"]
    assert _refs(graph, "mcp_server") == []


def test_claude_code_subagents_are_not_imported(tmp_path):
    _write(tmp_path / ".claude" / "agents" / "reviewer.md", "Review.\n")

    assert _refs(_declared(tmp_path), "agent") == []


def test_hooks_from_the_v1_file_and_config_json(tmp_path):
    _write(
        tmp_path / ".devin" / "hooks.v1.json",
        '{"PreToolUse": [{"matcher": "exec", "hooks": [{"type": "command", '
        '"command": "./check.sh"}]}]}',
    )
    _write_json(
        tmp_path / ".devin" / "config.json",
        {"hooks": {"PostCompaction": [{"hooks": [{"type": "command", "command": "echo c"}]}]}},
    )
    _write_json(
        tmp_path / ".devin" / "config.local.json",
        {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "echo e"}]}]}},
    )

    events = sorted(r.extra["event"] for r in _refs(_declared(tmp_path), "hook"))

    assert events == ["PostCompaction", "PreToolUse", "SessionEnd"]


def test_gitignored_surfaces_are_skipped_unless_asked_for(tmp_path):
    _write(tmp_path / ".gitignore", "vendor/\n")
    _skill(tmp_path / "vendor" / ".devin" / "skills" / "vendored", "vendored")

    assert _refs(_declared(tmp_path), "skill") == []
    assert _names(_declared(tmp_path, include_gitignored=True), "skill") == ["vendored"]


# --- Declared: plugins ----------------------------------------------------------


def test_a_devin_plugin_manifest_realizes_with_its_bundle(tmp_path):
    plugin = tmp_path / "review-tools"
    _write_json(plugin / ".devin-plugin" / "plugin.json", {"name": "review-tools"})
    _skill(plugin / "skills" / "review", "review")
    _write_json(plugin / ".mcp.json", {"mcpServers": {"bundled": _server()}})
    # Fixture content inside the bundle is the plugin's, not the tree's.
    _write_json(
        plugin / "examples" / ".devin" / "mcp_config.json", {"mcpServers": {"x": _server()}}
    )

    graph = _declared(tmp_path)
    (plugin_node,) = [n for n in graph.nodes.values() if n.kind == "plugin"]
    children = {c.kind: c for c in graph.children_of(plugin_node)}

    assert plugin_node.ref is not None and plugin_node.ref.name == "review-tools"
    assert set(children) == {"skill", "mcp_server"}
    assert _mcp(graph) == [("bundled", "review-tools/.mcp.json")]


def test_the_devin_manifest_wins_over_a_claude_manifest_in_one_root(tmp_path):
    _write_json(tmp_path / "p" / ".devin-plugin" / "plugin.json", {"name": "devin-name"})
    _write_json(tmp_path / "p" / ".claude-plugin" / "plugin.json", {"name": "claude-name"})

    assert _names(_declared(tmp_path), "plugin") == ["devin-name"]


def test_a_devin_plugin_name_outside_devins_grammar_does_not_qualify(tmp_path):
    """Devin requires lowercase alphanumerics separated by a single `-` or
    `.`; a name outside that grammar is one Devin itself rejects. Devin falls
    back to `.claude-plugin/plugin.json` only "if there's no
    `.devin-plugin/plugin.json`" (bundled `plugins/overview.mdx`), so a
    present but invalid Devin manifest leaves the root with no plugin rather
    than handing it to the Claude manifest."""
    _write_json(tmp_path / "p" / ".devin-plugin" / "plugin.json", {"name": "Bad_Name"})
    _write_json(tmp_path / "p" / ".claude-plugin" / "plugin.json", {"name": "claude-name"})

    assert _names(_declared(tmp_path), "plugin") == []


_REJECTED_DEVIN_MANIFESTS = (
    '{"name": "Bad_Name"}',
    '{"name": "p", "skills": ["../outside"]}',
    "{not json",
    "[]",
)


def test_an_invalid_devin_manifest_blocks_the_claude_fallback(tmp_path):
    for index, devin_manifest in enumerate(_REJECTED_DEVIN_MANIFESTS):
        root = tmp_path / str(index)
        _write(root / ".devin-plugin" / "plugin.json", devin_manifest)
        _write_json(root / ".claude-plugin" / "plugin.json", {"name": "claude-name"})
    assert _names(_declared(tmp_path), "plugin") == []


def test_a_devin_manifest_devin_rejects_is_a_recorded_gap(tmp_path):
    """The rejected manifest is a surface Devin read and refused, not an
    absent one: the plugin it would have realized is missing from the graph,
    so composition says so and names the file."""
    for index, devin_manifest in enumerate(_REJECTED_DEVIN_MANIFESTS):
        _write(tmp_path / str(index) / ".devin-plugin" / "plugin.json", devin_manifest)
    gaps = _declared(tmp_path).warnings.gaps
    for index in range(len(_REJECTED_DEVIN_MANIFESTS)):
        manifest = str(tmp_path / str(index) / ".devin-plugin" / "plugin.json")
        assert sum(manifest in gap for gap in gaps) == 1, (index, gaps)


def test_a_claude_manifest_still_loads_where_there_is_no_devin_manifest(tmp_path):
    _write_json(tmp_path / "p" / ".claude-plugin" / "plugin.json", {"name": "claude-name"})
    assert _names(_declared(tmp_path), "plugin") == ["claude-name"]


def test_an_agent_plugins_bundle_reads_dot_mcp_json_first_then_mcp_json(tmp_path):
    """For Agent Plugins, Devin reads root `mcp.json` "after `.mcp.json`,
    which wins on a server-name collision" (bundled `plugins/overview.mdx`)."""
    root = tmp_path / "portable"
    _write_json(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "portable",
        },
    )
    _write_json(root / ".mcp.json", {"mcpServers": {"dot": _server(), "shared": _server("dot")}})
    _write_json(root / "mcp.json", {"mcpServers": {"plain": _server(), "shared": _server("plain")}})

    assert _mcp(_declared(tmp_path)) == [
        ("dot", "portable/.mcp.json"),
        ("plain", "portable/mcp.json"),
        ("shared", "portable/.mcp.json"),
    ]


def test_a_devin_plugin_agent_declares_no_mcp_servers_or_hooks(tmp_path):
    """Plugin subagents use Devin's custom subagent format, whose frontmatter
    is `name`, `description`, `model`, `allowed-tools`/`tools` and
    `max-nesting` (bundled `subagents.mdx`): no `mcpServers`, no `hooks`."""
    plugin = tmp_path / "p"
    _write_json(plugin / ".devin-plugin" / "plugin.json", {"name": "p"})
    _write(
        plugin / "agents" / "reviewer.md",
        "---\nname: reviewer\nmcpServers:\n  sneaky:\n    command: npx\n"
        '    args: ["-y", "sneaky-mcp"]\n---\nReview.\n',
    )
    graph = _declared(tmp_path)
    assert _names(graph, "agent") == ["reviewer"]
    assert _mcp(graph) == []


def test_a_devin_plugin_has_no_commands_surface(tmp_path):
    """Devin generates `/<plugin>:<skill>` slash commands from skills; it has
    no `commands/` directory. A plugin that happens to ship one anyway (a
    fixture, an unrelated folder) must not have its contents reported as
    command components."""
    plugin = tmp_path / "p"
    _write_json(plugin / ".devin-plugin" / "plugin.json", {"name": "p"})
    _write(plugin / "commands" / "unrelated.md", "---\ndescription: not a command\n---\nx\n")

    assert _refs(_declared(tmp_path), "command") == []


def test_a_devin_plugin_directory_form_agent_is_named_for_its_directory(tmp_path):
    """`agents/<name>/AGENT.md` is Devin's directory-form subagent, named for
    its directory, not parsed as a flat file named `AGENT` -- the same rule
    `_add_agents_dir` already applies to a native (non-plugin) `agents/`."""
    plugin = tmp_path / "p"
    _write_json(plugin / ".devin-plugin" / "plugin.json", {"name": "p"})
    _write(plugin / "agents" / "reviewer" / "AGENT.md", "Review.\n")

    assert _names(_declared(tmp_path), "agent") == ["reviewer"]


def test_a_devin_plugin_directory_form_agent_honors_filename_precedence(tmp_path):
    """`AGENT.md` wins over `AGENTS.md` in the same directory."""
    plugin = tmp_path / "p"
    _write_json(plugin / ".devin-plugin" / "plugin.json", {"name": "p"})
    agent_dir = plugin / "agents" / "reviewer"
    _write(agent_dir / "AGENT.md", "Primary.\n")
    _write(agent_dir / "AGENTS.md", "Fallback.\n")

    assert len(_refs(_declared(tmp_path), "agent")) == 1


def test_a_devin_plugin_skills_list_replaces_the_default_directory(tmp_path):
    """A list-valued `skills` field names the plugin's skill roots outright,
    replacing (not adding to) the default `skills/` directory
    (docs.devin.ai/cli/extensibility/plugins/overview)."""
    plugin = tmp_path / "p"
    _write_json(
        plugin / ".devin-plugin" / "plugin.json",
        {"name": "p", "skills": ["custom-skills", "extra/skills"]},
    )
    _skill(plugin / "skills" / "default", "default")
    _skill(plugin / "custom-skills" / "custom", "custom")
    _skill(plugin / "extra" / "skills" / "extra", "extra")

    assert _names(_declared(tmp_path), "skill") == ["custom", "extra"]


def test_a_devin_plugin_empty_skills_list_disables_skill_loading(tmp_path):
    """`"skills": []` disables skill loading for the plugin entirely."""
    plugin = tmp_path / "p"
    _write_json(plugin / ".devin-plugin" / "plugin.json", {"name": "p", "skills": []})
    _skill(plugin / "skills" / "default", "default")

    assert _refs(_declared(tmp_path), "skill") == []


def test_a_devin_plugin_skills_list_with_one_invalid_entry_loads_none(tmp_path):
    """Devin fails the whole `skills` declaration when any entry doesn't
    resolve to a directory inside the plugin, rather than loading the
    entries that do (docs.devin.ai/cli/extensibility/plugins/overview)."""
    plugin = tmp_path / "p"
    _write_json(
        plugin / ".devin-plugin" / "plugin.json",
        {"name": "p", "skills": ["custom-skills", "../outside"]},
    )
    _skill(plugin / "skills" / "default", "default")
    _skill(plugin / "custom-skills" / "custom", "custom")

    assert _refs(_declared(tmp_path), "skill") == []


def test_a_devin_plugin_skills_string_still_replaces_the_default(tmp_path):
    plugin = tmp_path / "p"
    _write_json(plugin / ".devin-plugin" / "plugin.json", {"name": "p", "skills": "custom-skills"})
    _skill(plugin / "skills" / "default", "default")
    _skill(plugin / "custom-skills" / "custom", "custom")

    assert _names(_declared(tmp_path), "skill") == ["custom"]


# Plugin `mcpServers` source selection, as Devin CLI 3000.11.3's bundled
# `extensibility/plugins/overview.mdx` documents it: four shapes, the root
# `.mcp.json` convention, suppression by `exclusive` or a non-empty inline map,
# dropped unsafe paths, an invalid field disabling only MCP, first source wins.


def test_a_disabled_server_in_an_earlier_plugin_mcp_source_shadows_a_later_one(tmp_path):
    """First source wins by server name, and a disabled entry still names its
    server: Devin merges by name and then skips the disabled one, so a later
    source cannot bring it back. The same order holds between config levels,
    where the 3000.11.3 binary lists a project `"disabled": true` entry over a
    user definition of the same name."""
    disabled = {"command": "npx", "args": ["-y", "pkg-mcp@1.0.0"], "disabled": True}
    devin = tmp_path / "devin"
    _write_json(
        devin / ".devin-plugin" / "plugin.json", {"name": "devin", "mcpServers": ["a.json"]}
    )
    _write_json(devin / "a.json", {"mcpServers": {"shared": disabled}})
    _write_json(devin / ".mcp.json", {"mcpServers": {"shared": _server(), "solo": _server()}})
    portable = tmp_path / "portable"
    _write_json(
        portable / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "portable",
        },
    )
    _write_json(portable / ".mcp.json", {"mcpServers": {"shared": disabled}})
    _write_json(portable / "mcp.json", {"mcpServers": {"shared": _server(), "plain": _server()}})

    assert _mcp(_declared(tmp_path)) == [
        ("plain", "portable/mcp.json"),
        ("solo", "devin/.mcp.json"),
    ]


def _mcp_plugin(tmp_path: Path, servers: object) -> Path:
    plugin = tmp_path / "p"
    manifest: dict = {"name": "p"}
    if servers is not _ABSENT:
        manifest["mcpServers"] = servers
    _write_json(plugin / ".devin-plugin" / "plugin.json", manifest)
    _write_json(
        plugin / ".mcp.json", {"mcpServers": {"root": _server(), "shared": _server("root")}}
    )
    _write_json(
        plugin / "config" / "a.json", {"mcpServers": {"a": _server(), "shared": _server("a")}}
    )
    _write_json(plugin / "config" / "b.json", {"mcpServers": {"b": _server()}})
    _skill(plugin / "skills" / "kept", "kept")
    return plugin


_ABSENT = object()


def _mcp_names(graph: Graph) -> list[tuple[str, str]]:
    return [(name, manifest.split("/", 1)[-1]) for name, manifest in _mcp(graph)]


def test_a_devin_plugin_without_mcp_servers_reads_the_root_convention(tmp_path):
    _mcp_plugin(tmp_path, _ABSENT)
    assert _mcp_names(_declared(tmp_path)) == [("root", ".mcp.json"), ("shared", ".mcp.json")]


def test_a_devin_plugin_mcp_list_reads_each_file_in_order_then_the_root(tmp_path):
    _mcp_plugin(tmp_path, ["config/a.json", "config/b.json"])
    assert _mcp_names(_declared(tmp_path)) == [
        ("a", "a.json"),
        ("b", "b.json"),
        ("root", ".mcp.json"),
        # First source wins: `a.json` precedes the root convention.
        ("shared", "a.json"),
    ]


def test_a_devin_plugin_mcp_string_is_one_declaration_file(tmp_path):
    _mcp_plugin(tmp_path, "config/b.json")
    assert ("b", "b.json") in _mcp_names(_declared(tmp_path))


def test_an_exclusive_devin_plugin_mcp_declaration_suppresses_the_root(tmp_path):
    _mcp_plugin(tmp_path, {"paths": ["config/a.json"], "exclusive": True})
    assert _mcp_names(_declared(tmp_path)) == [("a", "a.json"), ("shared", "a.json")]


def test_a_non_exclusive_paths_declaration_keeps_the_root(tmp_path):
    _mcp_plugin(tmp_path, {"paths": ["config/b.json"]})
    assert _mcp_names(_declared(tmp_path)) == [
        ("b", "b.json"),
        ("root", ".mcp.json"),
        ("shared", ".mcp.json"),
    ]


def test_a_non_empty_inline_devin_plugin_mcp_map_suppresses_the_root(tmp_path):
    _mcp_plugin(tmp_path, {"inline": _server()})
    assert _mcp_names(_declared(tmp_path)) == [("inline", "plugin.json")]


def test_an_empty_inline_map_or_list_leaves_the_root_enabled(tmp_path):
    for value in ({}, []):
        _mcp_plugin(tmp_path, value)
        assert _mcp_names(_declared(tmp_path)) == [
            ("root", ".mcp.json"),
            ("shared", ".mcp.json"),
        ]


def test_an_unsafe_devin_plugin_mcp_path_is_dropped_not_fatal(tmp_path):
    _mcp_plugin(tmp_path, ["../outside.json", "/etc/mcp.json", "~/mcp.json", "config/b.json"])
    graph = _declared(tmp_path)
    assert ("b", "b.json") in _mcp_names(graph)
    assert _names(graph, "plugin") == ["p"]


def test_an_invalid_devin_plugin_mcp_field_disables_only_mcp(tmp_path):
    _mcp_plugin(tmp_path, 42)
    graph = _declared(tmp_path)
    assert _mcp(graph) == []
    assert _names(graph, "skill") == ["kept"]


def test_a_devin_plugin_with_an_invalid_skills_entry_does_not_load_at_all(tmp_path):
    """ "An invalid entry fails the whole manifest", where an invalid
    `mcpServers` only disables MCP: the plugin Devin rejects is not realized,
    so neither it nor anything it bundles is reported as a plugin's."""
    for invalid in (["custom", "../outside"], ["/abs"], ["~/skills"], [7], 7):
        plugin = tmp_path / "p"
        _write_json(plugin / ".devin-plugin" / "plugin.json", {"name": "p", "skills": invalid})
        _write_json(plugin / ".mcp.json", {"mcpServers": {"root": _server()}})
        _skill(plugin / "custom" / "c", "c")
        graph = _declared(tmp_path)
        assert _names(graph, "plugin") == [], invalid
        assert _names(graph, "skill") == [], invalid
        assert not [
            n for n in graph.nodes.values() if n.kind == "mcp_server" and "(inlined)" in n.key
        ], invalid


# --- Installed --------------------------------------------------------------------


def _home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def _config(tmp_path: Path) -> Path:
    return _home(tmp_path) / ".config" / "devin"


def test_installed_layers_merge_by_name_with_the_project_local_layer_winning(tmp_path):
    project = tmp_path / "project"
    _write_json(
        _config(tmp_path) / "mcp_config.json",
        {"mcpServers": {"github": _server(), "user-only": _server()}},
    )
    _write_json(project / ".devin" / "mcp_config.json", {"mcpServers": {"github": _server()}})
    _write_json(project / ".devin" / "mcp_config.local.json", {"mcpServers": {"github": _server()}})

    assert _mcp(_installed(tmp_path, project)) == [
        ("github", ".devin/mcp_config.local.json"),
        ("user-only", "devin/mcp_config.json"),
    ]


def test_installed_reads_every_home_import(tmp_path):
    home = _home(tmp_path)
    _config(tmp_path).mkdir(parents=True)
    _write_json(home / ".claude.json", {"mcpServers": {"claude-json": _server()}})
    _write_json(home / ".claude" / "settings.json", {"mcpServers": {"claude-settings": _server()}})
    _write_json(
        home / ".claude" / "mcp_servers.json", {"mcpServers": {"claude-servers": _server()}}
    )
    _write_json(
        home / ".codeium" / "windsurf" / "mcp_config.json",
        {"mcpServers": {"windsurf": {"serverUrl": "https://ws.example.test/mcp"}}},
    )
    # Non-stable Windsurf channels are deferred.
    _write_json(
        home / ".codeium" / "windsurf-next" / "mcp_config.json",
        {"mcpServers": {"windsurf-next": _server()}},
    )

    assert {name for name, _ in _mcp(_installed(tmp_path))} == {
        "claude-json",
        "claude-servers",
        "claude-settings",
        "windsurf",
    }


def test_the_user_switch_gates_home_imports_and_a_project_switch_overrides_it(tmp_path):
    home = _home(tmp_path)
    project = tmp_path / "project"
    _write_json(_config(tmp_path) / "config.json", {"read_config_from": {"claude": False}})
    _write_json(home / ".claude" / "settings.json", {"mcpServers": {"claude-settings": _server()}})
    project.mkdir()

    assert _mcp(_installed(tmp_path)) == []
    assert _mcp(_installed(tmp_path, project)) == []

    _write_json(project / ".devin" / "config.json", {"read_config_from": {"claude": True}})
    assert {name for name, _ in _mcp(_installed(tmp_path, project))} == {"claude-settings"}


def test_installed_skill_roots(tmp_path):
    home = _home(tmp_path)
    project = tmp_path / "project"
    _skill(_config(tmp_path) / "skills" / "user-skill", "user-skill")
    _skill(home / ".config" / "cognition" / "skills" / "legacy-skill", "legacy-skill")
    _skill(home / ".agents" / "skills" / "shared-skill", "shared-skill")
    _skill(home / ".codeium" / "windsurf" / "skills" / "ws-skill", "ws-skill")
    _skill(home / ".copilot" / "skills" / "team" / "copilot-skill", "copilot-skill")
    _skill(project / ".devin" / "skills" / "project-skill", "project-skill")
    _skill(project / ".claude" / "skills" / "claude-skill", "claude-skill")
    # Not a Devin root: Claude Code's user skills are not imported.
    _skill(home / ".claude" / "skills" / "claude-user-skill", "claude-user-skill")

    assert _names(_installed(tmp_path, project), "skill") == [
        "claude-skill",
        "copilot-skill",
        "legacy-skill",
        "project-skill",
        "shared-skill",
        "user-skill",
        "ws-skill",
    ]


def test_a_legacy_root_symlinked_to_the_config_root_is_one_skill(tmp_path):
    """The binary renamed `cognition/` to `devin/` and left a symlink behind."""
    home = _home(tmp_path)
    _skill(_config(tmp_path) / "skills" / "user-skill", "user-skill")
    (home / ".config" / "cognition").symlink_to(_config(tmp_path), target_is_directory=True)

    graph = _installed(tmp_path)

    assert _names(graph, "skill") == ["user-skill"]
    (node,) = [n for n in graph.nodes.values() if n.kind == "skill"]
    assert node.key.startswith("devin-cli/skills/user-skill/SKILL.md#")


def test_installed_subagents_hooks_and_commands(tmp_path):
    home = _home(tmp_path)
    project = tmp_path / "project"
    _write(_config(tmp_path) / "agents" / "reviewer.md", "Review.\n")
    _write(project / ".agents" / "agents" / "researcher" / "AGENT.md", "Research.\n")
    _write_json(
        _config(tmp_path) / "config.json",
        {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo u"}]}]}},
    )
    _write(
        project / ".devin" / "hooks.v1.json",
        '{"Stop": [{"hooks": [{"type": "command", "command": "echo p"}]}]}',
    )
    _write_json(
        home / ".claude" / "settings.json",
        {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo c"}]}]}},
    )
    _write(project / ".claude" / "commands" / "deploy.md", "Deploy.\n")

    graph = _installed(tmp_path, project)

    assert _names(graph, "agent") == ["researcher", "reviewer"]
    assert sorted(r.extra["event"] for r in _refs(graph, "hook")) == [
        "PreToolUse",
        "SessionStart",
        "Stop",
    ]
    assert _names(graph, "command") == ["deploy"]


def test_store_plugins_are_presence_only_from_the_lockfile(tmp_path):
    _config(tmp_path).mkdir(parents=True)
    lock = _home(tmp_path) / ".local" / "share" / "devin" / "cli" / "plugins" / "lock.json"
    _write_json(
        lock,
        {
            "requirements": [],
            "resolved": [{"name": "review-tools"}, {"identity": "acme/not-a-plugin-name"}],
            "edges": [],
        },
    )

    graph = _installed(tmp_path)
    (ref,) = _refs(graph, "plugin")

    assert ref.name == "review-tools"
    # Occurrence-local: a self-declared name with no recorded source mints no
    # cross-BOM identity (ADR-0070).
    assert ref.component_identity is None
    # Enable state and marketplace are unknown, so neither is claimed.
    assert "enabled" not in ref.extra
    assert "marketplace" not in ref.extra
    # The entry whose name could not be read lowers coverage instead of
    # vanishing silently.
    assert graph.warnings.gaps


def test_a_malformed_lockfile_is_a_gap(tmp_path):
    _config(tmp_path).mkdir(parents=True)
    _write(_home(tmp_path) / ".local" / "share" / "devin" / "cli" / "plugins" / "lock.json", "{")

    graph = _installed(tmp_path)

    assert _refs(graph, "plugin") == []
    assert graph.warnings.gaps


def test_installed_node_keys_carry_no_home_path(tmp_path):
    """Every foreign root has a label, so no absolute path reaches a bom-ref."""
    home = _home(tmp_path)
    project = tmp_path / "project"
    _write_json(_config(tmp_path) / "mcp_config.json", {"mcpServers": {"a": _server()}})
    _write_json(home / ".claude.json", {"mcpServers": {"b": _server()}})
    _write_json(home / ".claude" / "settings.json", {"mcpServers": {"c": _server()}})
    _write_json(
        home / ".codeium" / "windsurf" / "mcp_config.json", {"mcpServers": {"d": _server()}}
    )
    _skill(home / ".copilot" / "skills" / "e", "e")
    _skill(home / ".agents" / "skills" / "f", "f")
    _skill(home / ".config" / "cognition" / "skills" / "g", "g")
    _write_json(
        home / ".local" / "share" / "devin" / "cli" / "plugins" / "lock.json",
        {"resolved": [{"name": "h"}]},
    )
    _write_json(project / ".devin" / "mcp_config.json", {"mcpServers": {"i": _server()}})

    graph = _installed(tmp_path, project)
    keys = [key for key in graph.nodes if key != graph.root.key]

    assert keys and not [key for key in keys if str(tmp_path) in key]
    prefixes = {key.split("/", 1)[0] for key in keys}
    assert prefixes == {
        "agents",
        "claude-code",
        "copilot",
        "devin-cli",
        "devin-cli-data",
        "devin-cli-legacy",
        "home",
        "project",
        "windsurf",
    }


# Installed project layers: Devin finds the project root by walking up from the
# working directory to a `.git` or `.jj` directory and loads every `.devin/`
# on the way, a nested one taking precedence over its ancestors (bundled
# `reference/configuration/global-vs-local.mdx`).


def _monorepo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    app = repo / "packages" / "app"
    app.mkdir(parents=True)
    return repo, app


def test_installed_reads_every_devin_layer_up_to_the_repository_root(tmp_path):
    repo, app = _monorepo(tmp_path)
    _config(tmp_path).mkdir(parents=True)
    _write_json(repo / ".devin" / "mcp_config.json", {"mcpServers": {"shared": _server()}})
    _write_json(app / ".devin" / "mcp_config.json", {"mcpServers": {"app": _server()}})
    _skill(repo / ".devin" / "skills" / "repo-skill", "repo-skill")
    _write(repo / ".devin" / "agents" / "repo-agent.md", "Agent.\n")

    graph = _installed(tmp_path, app)

    assert [name for name, _ in _mcp(graph)] == ["app", "shared"]
    assert "repo-skill" in _names(graph, "skill")
    assert "repo-agent" in _names(graph, "agent")
    keys = [key for key in graph.nodes if key != graph.root.key]
    assert not [key for key in keys if str(tmp_path) in key]


def test_a_nested_devin_layer_wins_over_its_ancestor_by_name(tmp_path):
    repo, app = _monorepo(tmp_path)
    _config(tmp_path).mkdir(parents=True)
    _write_json(repo / ".devin" / "mcp_config.local.json", {"mcpServers": {"s": _server("repo")}})
    _write_json(app / ".devin" / "mcp_config.json", {"mcpServers": {"s": _server("app")}})

    graph = _installed(tmp_path, app)

    (ref,) = _refs(graph, "mcp_server")
    assert "packages/app" in (ref.source_manifest or "")


def test_without_a_repository_marker_only_the_project_layer_is_read(tmp_path):
    outer = tmp_path / "outer"
    app = outer / "app"
    app.mkdir(parents=True)
    _config(tmp_path).mkdir(parents=True)
    _write_json(outer / ".devin" / "mcp_config.json", {"mcpServers": {"outer": _server()}})
    _write_json(app / ".devin" / "mcp_config.json", {"mcpServers": {"app": _server()}})

    assert [name for name, _ in _mcp(_installed(tmp_path, app))] == ["app"]


def test_an_ancestor_layer_mcp_server_resolves_its_launch_dependencies(tmp_path):
    """Dependencies resolve within the repository the layers come from, not
    only beneath `--project`."""
    repo, app = _monorepo(tmp_path)
    _config(tmp_path).mkdir(parents=True)
    _write_json(
        repo / ".devin" / "mcp_config.json",
        {"mcpServers": {"tool": {"command": "npx", "args": ["@scope/tool@1.2.3"]}}},
    )
    _write_json(
        repo / "packages" / "tool" / "package.json",
        {"name": "@scope/tool", "version": "1.2.3", "dependencies": {"left-pad": "1.0.0"}},
    )
    graph = _installed(tmp_path, app)
    (server_key,) = [k for k, n in graph.nodes.items() if n.kind == "mcp_server"]
    assert [e for e in graph.edges if e.parent == server_key]
