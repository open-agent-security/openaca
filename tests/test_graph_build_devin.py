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

    assert plugin_node.ref.name == "review-tools"
    assert set(children) == {"skill", "mcp_server"}
    assert _mcp(graph) == [("bundled", "review-tools/.mcp.json")]


def test_the_devin_manifest_wins_over_a_claude_manifest_in_one_root(tmp_path):
    _write_json(tmp_path / "p" / ".devin-plugin" / "plugin.json", {"name": "devin-name"})
    _write_json(tmp_path / "p" / ".claude-plugin" / "plugin.json", {"name": "claude-name"})

    assert _names(_declared(tmp_path), "plugin") == ["devin-name"]


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
