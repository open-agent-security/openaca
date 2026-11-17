"""OpenCode's composition builder (ADR-0083, ADR-0084,
docs/specs/opencode-agent-kind.md).

The merge order of `Config.loadInstanceState`, the MCP merge by name and its
`enabled` state, V2 lowering, npm plugin versions from OpenCode's own install,
agent and command names, skills, and installed keys that carry no host path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import pytest

from tools.graph import Graph
from tools.graph_build_opencode import build_opencode_declared_graph, build_opencode_installed_graph


@dataclass(frozen=True)
class _FakeAgent:
    """Duck-typed `AgentInstance`: the builder must not import
    `tools.agent_kinds` (ADR-0044's one-way dependency)."""

    source: Literal["declared", "installed"]
    scan_root: Optional[Path] = None
    config_root: Optional[Path] = None
    project_root: Optional[Path] = None
    bom_ref: str = "root/opencode"
    root_label: str = "opencode"


@dataclass
class _Env:
    home: Path
    config: Path
    cache: Path
    managed: Path
    project: Path


@pytest.fixture
def env(tmp_path) -> _Env:
    home = tmp_path / "home"
    project = tmp_path / "work" / "repo"
    (project / ".git").mkdir(parents=True)
    config = home / ".config" / "opencode"
    config.mkdir(parents=True)
    return _Env(
        home=home,
        config=config,
        cache=home / ".cache" / "opencode",
        managed=tmp_path / "etc" / "opencode",
        project=project,
    )


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _json(path: Path, data: object) -> Path:
    return _write(path, json.dumps(data))


def _local(*command: str) -> dict:
    return {"type": "local", "command": list(command)}


def _installed(env: _Env, *, project: Path | None = None, warnings: list | None = None) -> Graph:
    graph = build_opencode_installed_graph(
        _FakeAgent(source="installed", config_root=env.config, project_root=project or env.project),
        cache_root=env.cache,
        managed_root=env.managed,
        home=env.home,
        warnings=warnings,
    )
    graph.validate()
    return graph


def _declared(scan_root: Path, *, include_gitignored: bool = False) -> Graph:
    graph = build_opencode_declared_graph(
        _FakeAgent(source="declared", scan_root=scan_root), include_gitignored=include_gitignored
    )
    graph.validate()
    return graph


def _refs(graph: Graph, kind: str) -> list:
    return [n.ref for n in graph.nodes.values() if n.kind == kind and n.ref is not None]


def _servers(graph: Graph) -> dict[str, list]:
    out: dict[str, list] = {}
    for ref in _refs(graph, "mcp_server"):
        out.setdefault(ref.extra["component_path"][-1]["name"], []).append(ref)
    return out


def _server(graph: Graph, name: str):
    (ref,) = _servers(graph)[name]
    return ref


def _names(graph: Graph, kind: str) -> list[str]:
    return sorted(ref.name for ref in _refs(graph, kind))


# --- MCP servers: the merge by name -------------------------------------------------


def test_global_files_merge_in_load_global_order(env):
    """`config.json`, then `opencode.json`, then `opencode.jsonc`: the last
    file that sets `type` owns the server."""
    _json(env.config / "config.json", {"mcp": {"a": _local("first")}})
    _json(env.config / "opencode.json", {"mcp": {"a": _local("second")}})
    _write(
        env.config / "opencode.jsonc",
        '{\n  // comment\n  "mcp": {"a": {"type": "local", "command": ["third"]},},\n}\n',
    )

    ref = _server(_installed(env), "a")

    assert ref.source_manifest == str(env.config / "opencode.jsonc")
    assert ref.extra["install_source"] == "third"
    assert ref.extra["enabled"] is True


def test_a_later_enabled_toggle_switches_a_server_off_and_keeps_it(env):
    """ADR-0083: one component per name, `enabled` always stated, attributed to
    the file that declares the server."""
    _json(env.config / "opencode.json", {"mcp": {"github": _local("npx", "-y", "gh-mcp@1.0.0")}})
    _json(env.project / "opencode.json", {"mcp": {"github": {"enabled": False}}})

    ref = _server(_installed(env), "github")

    assert ref.extra["enabled"] is False
    assert ref.source_manifest == str(env.config / "opencode.json")
    assert ref.source_locator == "$.mcp.github"
    assert ref.extra["opencode_layers"] == [
        str(env.config / "opencode.json"),
        str(env.project / "opencode.json"),
    ]


def test_a_toggle_with_nothing_to_toggle_is_no_component(env):
    """`MCP.state` ignores a merged entry with no `type`."""
    _json(env.project / "opencode.json", {"mcp": {"ghost": {"enabled": True}}})

    assert "ghost" not in _servers(_installed(env))


def test_a_remote_server_keeps_its_configured_name_as_the_alias(env):
    _json(
        env.config / "opencode.json",
        {"mcp": {"my.docs": {"type": "remote", "url": "https://docs.example.com/mcp"}}},
    )

    ref = _server(_installed(env), "my.docs")

    assert ref.component_identity == "mcp-remote/docs.example.com/mcp"
    assert ref.source_locator == "$.mcp.my.docs"
    assert ref.extra["transport"] == "http"


def test_a_package_launch_is_matchable_by_its_purl(env):
    _json(
        env.config / "opencode.json",
        {"mcp": {"git": _local("npx", "-y", "@cyanheads/git-mcp-server@1.1.0")}},
    )

    ref = _server(_installed(env), "git")

    assert ref.purl == "pkg:npm/%40cyanheads/git-mcp-server@1.1.0"
    assert ref.extra["install_source"] == "npx -y @cyanheads/git-mcp-server@1.1.0"


def test_the_v2_servers_envelope_is_lowered_and_a_flat_entry_wins(env):
    _json(
        env.config / "opencode.json",
        {
            "mcp": {
                "timeout": {"catalog": 5, "execution": 5},
                "flat": _local("flat-server"),
                "servers": {
                    "flat": _local("envelope-server"),
                    "nested": {**_local("nested-server"), "disabled": True},
                },
            }
        },
    )

    servers = _servers(_installed(env))

    assert set(servers) == {"flat", "nested"}
    assert servers["flat"][0].extra["install_source"] == "flat-server"
    (nested,) = servers["nested"]
    assert nested.extra["enabled"] is False
    assert nested.source_locator == "$.mcp.servers.nested"


def test_a_flat_v2_entry_with_disabled_is_lowered(env):
    _json(env.config / "opencode.json", {"mcp": {"off": {**_local("x"), "disabled": True}}})

    assert _server(_installed(env), "off").extra["enabled"] is False


def test_a_rejected_file_is_a_gap_and_contributes_nothing(env):
    """OpenCode validates a whole file and does not start on a rejected one."""
    _json(
        env.config / "opencode.json",
        {"mcp": {"good": _local("good-server"), "bad": {"type": "local"}}},
    )
    warnings: list[str] = []

    graph = _installed(env, warnings=warnings)

    assert _servers(graph) == {}
    assert graph.warnings.gaps
    assert any("opencode.json" in w for w in warnings)


def test_ancestor_dot_opencode_overrides_a_nearer_one(env):
    """`ConfigPaths.directories` lists `.opencode` nearest first, and later
    merges win (docs/specs/opencode-agent-kind.md, Where the documentation and
    the code disagree)."""
    nested = env.project / "packages" / "app"
    _json(nested / ".opencode" / "opencode.json", {"mcp": {"s": _local("near")}})
    _json(env.project / ".opencode" / "opencode.json", {"mcp": {"s": _local("far")}})

    ref = _server(_installed(env, project=nested), "s")

    assert ref.extra["install_source"] == "far"
    assert ref.source_manifest == str(env.project / ".opencode" / "opencode.json")


def test_project_files_merge_farthest_first(env):
    nested = env.project / "packages" / "app"
    _json(nested / "opencode.json", {"mcp": {"s": _local("near")}})
    _json(env.project / "opencode.json", {"mcp": {"s": _local("far")}})

    assert _server(_installed(env, project=nested), "s").extra["install_source"] == "near"


def test_layers_stop_at_the_git_root(env):
    _json(env.project.parent / "opencode.json", {"mcp": {"outside": _local("x")}})

    assert "outside" not in _servers(_installed(env))


def test_a_project_without_git_is_its_own_only_layer(env, tmp_path):
    loose = tmp_path / "loose" / "dir"
    _json(loose.parent / "opencode.json", {"mcp": {"parent": _local("x")}})
    _json(loose / "opencode.json", {"mcp": {"own": _local("y")}})

    assert set(_servers(_installed(env, project=loose))) == {"own"}


def test_the_managed_directory_merges_last(env):
    _json(env.project / "opencode.json", {"mcp": {"s": _local("project")}})
    _json(env.managed / "opencode.json", {"mcp": {"s": {"enabled": False}}})

    ref = _server(_installed(env), "s")

    assert ref.extra["enabled"] is False
    assert ref.source_manifest == str(env.project / "opencode.json")


# --- Plugins -----------------------------------------------------------------------


def _install(env: _Env, key: str, directory: str, name: str, version: str) -> None:
    _json(
        env.cache / "packages" / key / "node_modules" / directory / "package.json",
        {"name": name, "version": version},
    )


def _plugins(graph: Graph) -> dict:
    return {ref.name: ref for ref in _refs(graph, "plugin")}


def test_an_npm_plugin_takes_its_version_from_opencodes_install(env):
    """ADR-0084: a bare name installs as `<name>@latest`, and the installed
    `package.json` gives the version that runs."""
    _json(env.config / "opencode.json", {"plugin": ["opencode-wakatime", "@acme/tool@^1.0.0"]})
    _install(env, "opencode-wakatime@latest", "opencode-wakatime", "opencode-wakatime", "1.2.3")
    _install(env, "@acme/tool@^1.0.0", "@acme/tool", "@acme/tool", "1.4.0")

    plugins = _plugins(_installed(env))

    wakatime = plugins["opencode-wakatime"]
    assert wakatime.purl == "pkg:npm/opencode-wakatime@1.2.3"
    assert wakatime.component_identity == "plugin/npm/opencode-wakatime"
    assert wakatime.source_locator == "$.plugin[0]"
    assert plugins["@acme/tool"].purl == "pkg:npm/%40acme/tool@1.4.0"


def test_an_uninstalled_plugin_has_only_an_exact_pinned_version(env):
    _json(env.config / "opencode.json", {"plugin": ["pinned@2.0.1", "ranged@^2", "bare"]})

    plugins = _plugins(_installed(env))

    assert plugins["pinned"].version == "2.0.1"
    assert plugins["ranged"].version is None
    assert plugins["bare"].version is None
    assert plugins["bare"].component_identity == "plugin/npm/bare"


def test_an_alias_spec_is_the_package_it_installs(env):
    _json(env.config / "opencode.json", {"plugin": ["mine@npm:real-plugin@3.1.0"]})
    _install(env, "mine@npm:real-plugin@3.1.0", "mine", "real-plugin", "3.1.0")

    (plugin,) = _refs(_installed(env), "plugin")

    assert plugin.purl == "pkg:npm/real-plugin@3.1.0"


def test_the_later_declaration_of_a_package_wins(env):
    """`deduplicatePluginOrigins` keys on the package name alone."""
    _json(env.config / "opencode.json", {"plugin": ["dup@1.0.0"]})
    _json(env.project / "opencode.json", {"plugin": [["dup@2.0.0", {"option": True}]]})

    (plugin,) = _refs(_installed(env), "plugin")

    assert plugin.version == "2.0.0"
    assert plugin.source_manifest == str(env.project / "opencode.json")


def test_deprecated_and_non_registry_specs(env):
    """OpenCode ignores the two packages now built in. A Git spec has no
    registry name, so it is presence-only, with no identity."""
    _json(
        env.config / "opencode.json",
        {"plugin": ["opencode-copilot-auth@0.0.1", "github:owner/repo"]},
    )

    (plugin,) = _refs(_installed(env), "plugin")

    assert plugin.name == "github:owner/repo"
    assert plugin.component_identity is None
    assert plugin.version is None


def test_local_plugin_files_and_a_path_spec_to_the_same_file(env):
    """A file in `plugins/` loads with no declaration, and a path spec that
    names the same file is the same plugin."""
    _write(env.config / "plugins" / "notify.ts", "export default {}\n")
    _write(env.project / ".opencode" / "plugin" / "guard.js", "export default {}\n")
    _write(env.project / ".opencode" / "plugin" / "readme.md", "x\n")
    _json(env.project / ".opencode" / "opencode.json", {"plugin": ["./plugin/guard.js"]})

    plugins = _plugins(_installed(env))

    assert set(plugins) == {"notify", "guard"}
    assert plugins["notify"].component_identity is None
    assert plugins["guard"].source_manifest == str(
        env.project / ".opencode" / "plugin" / "guard.js"
    )


# --- Agents and commands ---------------------------------------------------------------


def test_agents_are_named_as_the_task_tool_names_them(env):
    _json(env.config / "opencode.json", {"agent": {"reviewer": {"description": "Reviews"}}})
    _write(env.config / "agents" / "team" / "lead.md", "---\ndescription: Lead\n---\nLead.\n")
    _write(env.project / ".opencode" / "agent" / "x.md", "---\nname: renamed\n---\nBody.\n")
    _write(env.project / ".opencode" / "modes" / "focus.md", "---\n---\nFocus.\n")
    _json(env.project / "opencode.json", {"agents": {"v2": {"description": "V2"}}})

    assert _names(_installed(env), "agent") == ["focus", "renamed", "reviewer", "team/lead", "v2"]


def test_a_merged_disable_removes_every_definition_of_the_name(env):
    _write(env.config / "agents" / "helper.md", "---\ndescription: Helper\n---\nHelp.\n")
    _json(env.config / "opencode.json", {"agent": {"helper": {"model": "x/y"}}})
    _json(env.project / "opencode.json", {"agent": {"helper": {"disable": True}}})
    _json(env.managed / "opencode.json", {"agents": {"gone": {"disabled": True}}})

    assert _names(_installed(env), "agent") == []


def test_a_later_definition_can_re_enable_a_name(env):
    _json(env.config / "opencode.json", {"agent": {"helper": {"disable": True}}})
    _json(env.project / "opencode.json", {"agent": {"helper": {"disable": False}}})

    assert _names(_installed(env), "agent") == ["helper", "helper"]


def test_a_definition_named_for_a_built_in_is_a_component(env):
    """It decides how that agent runs; installed wins (ADR-0076)."""
    _json(env.config / "opencode.json", {"agent": {"build": {"prompt": "Mine."}}})

    assert _names(_installed(env), "agent") == ["build"]


def test_commands_from_configuration_and_markdown(env):
    _json(env.config / "opencode.json", {"command": {"test": {"template": "Run tests."}}})
    _write(env.project / ".opencode" / "commands" / "git" / "ship.md", "Ship it.\n")

    assert _names(_installed(env), "command") == ["git/ship", "test"]


def test_a_command_without_a_template_rejects_its_file(env):
    _json(env.config / "opencode.json", {"command": {"test": {}}, "mcp": {"a": _local("x")}})

    graph = _installed(env)

    assert _refs(graph, "command") == [] and _servers(graph) == {}
    assert graph.warnings.gaps


# --- Skills ---------------------------------------------------------------------------


def _skill(directory: Path, name: str | None) -> Path:
    head = f"name: {name}\n" if name is not None else "description: Unnamed\n"
    return _write(directory / "SKILL.md", f"---\n{head}---\nDo it.\n")


def test_every_skill_root_opencode_reads(env):
    _skill(env.home / ".claude" / "skills" / "group" / "nested", "claude-user")
    _skill(env.home / ".agents" / "skills" / ".hidden" / "x", "agents-hidden")
    _skill(env.config / "skills" / "config-skill", "config-skill")
    _skill(env.config / "skill" / ".hidden" / "x", "config-hidden")
    _skill(env.home / ".opencode" / "skills" / "home-skill", "home-skill")
    _skill(env.project / ".claude" / "skills" / "p", "claude-project")
    _skill(env.project / ".agents" / "skills" / "q", "agents-project")
    _skill(env.project / ".opencode" / "skills" / "r", "opencode-project")
    _skill(env.project / ".opencode" / "skills" / "unnamed", None)

    assert _names(_installed(env), "skill") == [
        "agents-hidden",
        "agents-project",
        "claude-project",
        "claude-user",
        "config-skill",
        "home-skill",
        "opencode-project",
    ]


def test_configured_skill_paths_and_urls(env, tmp_path):
    _skill(env.home / "team-skills" / "a", "from-home")
    _skill(env.project / "local-skills" / "b", "from-project")
    _skill(tmp_path / "opt" / "skills" / "c", "from-elsewhere")
    _json(
        env.config / "opencode.json",
        {
            "skills": {
                "paths": ["~/team-skills", "local-skills", str(tmp_path / "opt" / "skills")],
                "urls": ["https://skills.example.com/"],
            }
        },
    )

    graph = _installed(env)

    assert _names(graph, "skill") == ["from-elsewhere", "from-home", "from-project"]
    assert any("https://skills.example.com/" in gap for gap in graph.warnings.gaps)
    keys = [n.key for n in graph.nodes.values() if n.kind == "skill"]
    assert not [k for k in keys if str(tmp_path) in k]


def test_installed_keys_carry_no_host_path(env, tmp_path):
    _json(env.config / "opencode.json", {"mcp": {"a": _local("a")}, "plugin": ["p@1.0.0"]})
    _json(env.managed / "opencode.json", {"mcp": {"m": _local("m")}})
    _json(env.home / ".opencode" / "opencode.json", {"mcp": {"h": _local("h")}})
    _json(env.project / "opencode.json", {"mcp": {"p": _local("p")}})
    _skill(env.home / ".claude" / "skills" / "c", "c")

    keys = [n.key for n in _installed(env).nodes.values() if n.ref is not None]

    assert keys and not [k for k in keys if str(tmp_path) in k]
    assert {k.split("#")[0] for k in keys} >= {
        "opencode/opencode.json",
        "opencode-managed/opencode.json",
        "home/.opencode/opencode.json",
        "project/opencode.json",
        "claude-code/skills/c/SKILL.md",
    }


# --- Declared -----------------------------------------------------------------------


def test_declared_groups_compose_apart(tmp_path):
    """A repository has no session directory: each configured directory is
    its own group, merged as one layer, and two groups are two occurrences."""
    _json(tmp_path / "opencode.json", {"mcp": {"s": _local("root")}})
    _write(
        tmp_path / ".opencode" / "opencode.jsonc",
        '{"mcp": {"s": {"enabled": false}}, // switched off for the root\n}',
    )
    _json(tmp_path / "packages" / "app" / "opencode.json", {"mcp": {"s": _local("app")}})

    servers = _servers(_declared(tmp_path))

    by_source = {ref.source_manifest: ref for ref in servers["s"]}
    assert by_source[str(tmp_path / "opencode.json")].extra["enabled"] is False
    assert by_source[str(tmp_path / "packages" / "app" / "opencode.json")].extra["enabled"]


def test_declared_composition_reads_every_surface(tmp_path):
    _json(
        tmp_path / "opencode.json",
        {
            "plugin": ["pinned@1.0.0", "unpinned", "./tools/plugin.ts"],
            "agent": {"cfg-agent": {}},
            "command": {"cfg-command": {"template": "t"}},
            "skills": {"paths": ["team-skills", "../outside"]},
        },
    )
    _write(tmp_path / ".opencode" / "agents" / "md-agent.md", "A.\n")
    _write(tmp_path / ".opencode" / "commands" / "md-command.md", "C.\n")
    _write(tmp_path / ".opencode" / "plugins" / "auto.ts", "export default {}\n")
    _skill(tmp_path / ".opencode" / "skills" / "own", "own")
    _skill(tmp_path / ".claude" / "skills" / "claude", "claude")
    _skill(tmp_path / "team-skills" / "team", "team")
    _skill(tmp_path.parent / "outside" / "out", "out")

    graph = _declared(tmp_path)

    plugins = _plugins(graph)
    assert plugins["pinned"].version == "1.0.0" and plugins["unpinned"].version is None
    assert set(plugins) == {"pinned", "unpinned", "plugin", "auto"}
    assert _names(graph, "agent") == ["cfg-agent", "md-agent"]
    assert _names(graph, "command") == ["cfg-command", "md-command"]
    assert _names(graph, "skill") == ["claude", "own", "team"]
    keys = [n.key for n in graph.nodes.values() if n.ref is not None]
    assert not [k for k in keys if str(tmp_path) in k]


def test_declared_composition_respects_gitignore(tmp_path):
    _write(tmp_path / ".gitignore", "local/\n")
    _json(tmp_path / "local" / "opencode.json", {"mcp": {"hidden": _local("x")}})
    _json(tmp_path / "opencode.json", {"mcp": {"shown": _local("y")}})

    assert set(_servers(_declared(tmp_path))) == {"shown"}
    assert set(_servers(_declared(tmp_path, include_gitignored=True))) == {"hidden", "shown"}
