"""Pi's composition builder (ADR-0081, ADR-0082, docs/specs/pi-agent-kind.md).

Every surface in the spec's "Where each surface loads from" table, the merge of
the two `mcp.json` scopes, package installs and their versions, the trust gate
on the installed project layer, and installed node keys that carry no home
path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from tools.graph import Graph, WarningLog
from tools.graph_build_pi import build_pi_declared_graph, build_pi_installed_graph


@dataclass(frozen=True)
class _FakeAgent:
    """Duck-typed `AgentInstance`: the builder must not import
    `tools.agent_kinds` (ADR-0044's one-way dependency)."""

    source: Literal["declared", "installed"]
    scan_root: Optional[Path] = None
    config_root: Optional[Path] = None
    project_root: Optional[Path] = None
    bom_ref: str = "root/pi"
    root_label: str = "pi"


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_json(path: Path, data: object) -> Path:
    return _write(path, json.dumps(data))


def _skill(directory: Path, name: str | None = None, *, description: str = "Do it.") -> Path:
    name = name or directory.name
    return _write(
        directory / "SKILL.md", f"---\nname: {name}\ndescription: {description}\n---\nDo it.\n"
    )


def _declared(scan_root: Path, warnings: WarningLog | None = None) -> Graph:
    graph = build_pi_declared_graph(
        _FakeAgent(source="declared", scan_root=scan_root), warnings=warnings
    )
    graph.validate()
    return graph


def _installed(
    tmp_path: Path, project: Optional[Path] = None, warnings: WarningLog | None = None
) -> Graph:
    home = tmp_path / "home"
    graph = build_pi_installed_graph(
        _FakeAgent(source="installed", config_root=home / ".pi" / "agent", project_root=project),
        home=home,
        warnings=warnings,
    )
    graph.validate()
    return graph


def _agent_dir(tmp_path: Path) -> Path:
    path = tmp_path / "home" / ".pi" / "agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _refs(graph: Graph, kind: str) -> list:
    return [n.ref for n in graph.nodes.values() if n.kind == kind and n.ref is not None]


def _names(graph: Graph, kind: str) -> list[str]:
    return sorted(r.name for r in _refs(graph, kind))


def _servers(graph: Graph) -> dict[str, Any]:
    """Configured name -> ref, read from the walk's last entry (the alias)."""
    return {ref.extra["component_path"][-1]["name"]: ref for ref in _refs(graph, "mcp_server")}


def _children(graph: Graph, parent_ref) -> list:
    keys = {key for key, node in graph.nodes.items() if node.ref is parent_ref}
    (parent_key,) = keys
    return [graph.nodes[e.child].ref for e in graph.edges if e.parent == parent_key]


# --- Declared: MCP servers ---------------------------------------------------


def test_an_empty_repo_yields_only_the_root(tmp_path):
    graph = _declared(tmp_path)
    assert list(graph.nodes.values()) == [graph.root]


def test_a_server_is_named_by_its_configured_key(tmp_path):
    _write_json(
        tmp_path / ".pi" / "mcp.json",
        {
            "mcpServers": {
                "git-tools": {
                    "command": "npx",
                    "args": ["-y", "@cyanheads/git-mcp-server@1.1.0"],
                },
                "docs": {"url": "https://docs.example.test/mcp", "type": "streamable-http"},
            }
        },
    )

    servers = _servers(_declared(tmp_path))

    assert set(servers) == {"git-tools", "docs"}
    assert servers["git-tools"].purl == "pkg:npm/%40cyanheads/git-mcp-server@1.1.0"
    assert servers["git-tools"].extra["component_path"] == [
        {"type": "mcp_server", "name": "git-tools"}
    ]
    assert servers["docs"].extra["enabled"] is True


def test_a_byte_order_mark_is_allowed(tmp_path):
    _write(
        tmp_path / ".pi" / "mcp.json",
        "﻿" + json.dumps({"mcpServers": {"a": {"command": "uvx", "args": ["a-mcp"]}}}),
    )

    assert set(_servers(_declared(tmp_path))) == {"a"}


def test_a_disabled_server_is_kept_with_its_state(tmp_path):
    _write_json(
        tmp_path / ".pi" / "mcp.json",
        {"mcpServers": {"off": {"url": "https://off.example.test/mcp", "enabled": False}}},
    )

    assert _servers(_declared(tmp_path))["off"].extra["enabled"] is False


def test_entries_pi_rejects_are_not_components(tmp_path):
    warnings = WarningLog()
    _write_json(
        tmp_path / ".pi" / "mcp.json",
        {
            "mcpServers": {
                "bad name": {"command": "x"},
                "legacy": {"url": "https://sse.example.test/sse", "type": "sse"},
                "plain-http": {"url": "ftp://example.test/mcp"},
                "neither": {"description": "nothing to run"},
                "routed": {"url": "https://routed.example.test/mcp", "auth": {"provider": "p"}},
                "override-only": {"enabled": False},
                "a-b": {"command": "uvx", "args": ["a-b-mcp"]},
                "a_b": {"command": "uvx", "args": ["other-mcp"]},
            }
        },
    )

    servers = _servers(_declared(tmp_path, warnings))

    assert set(servers) == {"a-b"}
    assert not warnings.gaps
    assert len([w for w in warnings if "is not loaded" in w]) == 7


def test_a_malformed_mcp_file_is_a_gap(tmp_path):
    warnings = WarningLog()
    _write(tmp_path / ".pi" / "mcp.json", "{not json")

    _declared(tmp_path, warnings)

    assert len(warnings.gaps) == 1


def test_every_project_directory_in_a_repo_is_composed(tmp_path):
    _write_json(tmp_path / ".pi" / "mcp.json", {"mcpServers": {"root": {"command": "a"}}})
    _write_json(
        tmp_path / "packages" / "app" / ".pi" / "mcp.json",
        {"mcpServers": {"app": {"command": "b"}}},
    )

    assert set(_servers(_declared(tmp_path))) == {"root", "app"}


# --- Declared: extensions, skills, prompts ------------------------------------------


def test_extensions_follow_the_cli_discovery_rules(tmp_path):
    extensions = tmp_path / ".pi" / "extensions"
    _write(extensions / "gate.ts")
    _write(extensions / "audit.js")
    _write(extensions / "notes.txt")
    _write(extensions / "tool" / "index.ts")
    _write(extensions / "tool" / "helper.ts")
    _write_json(
        extensions / "pack" / "package.json",
        {"name": "pack", "pi": {"extensions": ["./src/a.ts", "./src/b.ts", "./missing.ts"]}},
    )
    _write(extensions / "pack" / "src" / "a.ts")
    _write(extensions / "pack" / "src" / "b.ts")
    _write(extensions / ".hidden.ts")
    _write(extensions / "node_modules" / "dep" / "index.js")

    plugins = _refs(_declared(tmp_path), "plugin")

    assert sorted(r.name for r in plugins) == ["a", "audit", "b", "gate", "tool"]
    pack = {r.name: r for r in plugins if r.source_manifest.endswith("package.json")}
    assert pack["a"].source_locator == "$.pi.extensions[0]"
    assert pack["b"].source_locator == "$.pi.extensions[1]"


def test_an_extensions_directory_with_its_own_entry_loads_only_that(tmp_path):
    extensions = tmp_path / ".pi" / "extensions"
    _write(extensions / "index.ts")
    _write(extensions / "other.ts")

    assert _names(_declared(tmp_path), "plugin") == ["extensions"]


def test_an_extension_directory_brings_its_dependencies_once(tmp_path):
    pack = tmp_path / ".pi" / "extensions" / "pack"
    _write_json(
        pack / "package.json",
        {
            "name": "pack",
            "pi": {"extensions": ["./a.ts", "./b.ts"]},
            "dependencies": {"left-pad": "1.3.0"},
        },
    )
    _write(pack / "a.ts")
    _write(pack / "b.ts")

    graph = _declared(tmp_path)

    assert [r.name for r in _refs(graph, "package")] == ["left-pad"]


def test_skills_are_walked_recursively_and_stop_at_a_skill(tmp_path):
    skills = tmp_path / ".pi" / "skills"
    _skill(skills / "deploy")
    _skill(skills / "group" / "review")
    _skill(skills / "deploy" / "nested")
    _skill(skills / ".hidden" / "skipped")
    _skill(skills / "node_modules" / "skipped")
    _skill(tmp_path / ".agents" / "skills" / "shared")

    assert _names(_declared(tmp_path), "skill") == ["deploy", "review", "shared"]


def test_a_skill_without_a_description_is_not_loaded(tmp_path):
    _write(tmp_path / ".pi" / "skills" / "bare" / "SKILL.md", "---\nname: bare\n---\nx\n")
    _skill(tmp_path / ".pi" / "skills" / "full")

    assert _names(_declared(tmp_path), "skill") == ["full"]


def test_prompts_are_direct_children_named_by_their_file(tmp_path):
    prompts = tmp_path / ".pi" / "prompts"
    _write(prompts / "review.md", "---\nname: something-else\ndescription: r\n---\nReview.\n")
    _write(prompts / "nested" / "deep.md", "Deep.\n")
    _write(prompts / "notes.txt", "x")

    assert _names(_declared(tmp_path), "command") == ["review"]


def test_settings_entries_add_paths_relative_to_the_pi_directory(tmp_path):
    _write_json(
        tmp_path / ".pi" / "settings.json",
        {
            "extensions": ["../tools/ext.ts", "!**/legacy.ts", "builtin:mcp"],
            "skills": ["../shared-skills"],
            "prompts": ["../prompts"],
        },
    )
    _write(tmp_path / "tools" / "ext.ts")
    _skill(tmp_path / "shared-skills" / "lint")
    _write(tmp_path / "prompts" / "deep" / "ship.md", "Ship.\n")

    graph = _declared(tmp_path)

    assert _names(graph, "plugin") == ["ext"]
    assert _names(graph, "skill") == ["lint"]
    # A settings directory is walked recursively, unlike `.pi/prompts`.
    assert _names(graph, "command") == ["ship"]


def test_a_settings_entry_outside_the_repo_is_a_gap(tmp_path):
    warnings = WarningLog()
    _write_json(
        tmp_path / "repo" / ".pi" / "settings.json",
        {"skills": ["~/skills", "../../outside"]},
    )
    _skill(tmp_path / "outside" / "x")

    graph = _declared(tmp_path / "repo", warnings)

    assert _names(graph, "skill") == []
    assert len(warnings.gaps) == 2


def test_gitignored_resources_are_skipped(tmp_path):
    _write(tmp_path / ".gitignore", ".pi/extensions/private.ts\n")
    _write(tmp_path / ".pi" / "extensions" / "private.ts")
    _write(tmp_path / ".pi" / "extensions" / "public.ts")

    assert _names(_declared(tmp_path), "plugin") == ["public"]


# --- Declared: packages ---------------------------------------------------------


def test_a_declared_npm_package_is_presence_only_with_a_gap(tmp_path):
    warnings = WarningLog()
    _write_json(
        tmp_path / ".pi" / "settings.json",
        {"packages": ["npm:@scope/tools@1.2.0", {"source": "npm:other@^2"}]},
    )

    graph = _declared(tmp_path, warnings)

    packages = {r.name: r for r in _refs(graph, "plugin")}
    assert packages["@scope/tools"].purl == "pkg:npm/%40scope/tools@1.2.0"
    assert packages["other"].version is None
    assert packages["other"].extra["install_source"] == "npm:other@^2"
    assert packages["@scope/tools"].extra["scope"] == "project"
    assert len(warnings.gaps) == 2


def test_a_declared_local_package_is_composed_from_the_repo(tmp_path):
    _write_json(tmp_path / ".pi" / "settings.json", {"packages": ["../tools"]})
    _write_json(tmp_path / "tools" / "package.json", {"name": "repo-tools", "version": "0.1.0"})
    _write(tmp_path / "tools" / "extensions" / "gate.ts")
    _skill(tmp_path / "tools" / "skills" / "lint")
    _write(tmp_path / "tools" / "prompts" / "ship.md", "Ship.\n")

    graph = _declared(tmp_path)

    (package,) = [r for r in _refs(graph, "plugin") if r.name == "repo-tools"]
    assert package.component_identity is None
    children = _children(graph, package)
    assert sorted((r.extra["component_type"], r.name) for r in children) == [
        ("command", "ship"),
        ("plugin", "gate"),
        ("skill", "lint"),
    ]
    assert all(
        r.extra["component_path"][0] == {"type": "plugin", "name": "repo-tools"} for r in children
    )


# --- Installed: MCP servers -----------------------------------------------------


def test_a_project_server_replaces_a_user_server_of_its_name(tmp_path):
    agent = _agent_dir(tmp_path)
    project = tmp_path / "project"
    _write_json(
        agent / "mcp.json",
        {
            "mcpServers": {
                "git": {"command": "npx", "args": ["git-mcp@1.0.0"]},
                "user": {"command": "u"},
            }
        },
    )
    _write_json(
        project / ".pi" / "mcp.json",
        {"mcpServers": {"git": {"command": "npx", "args": ["git-mcp@2.0.0"]}}},
    )

    servers = _servers(_installed(tmp_path, project))

    assert servers["git"].version == "2.0.0"
    assert Path(servers["git"].source_manifest).parent.name == ".pi"
    assert set(servers) == {"git", "user"}


def test_a_project_override_keeps_the_user_source(tmp_path):
    agent = _agent_dir(tmp_path)
    project = tmp_path / "project"
    _write_json(
        agent / "mcp.json",
        {
            "mcpServers": {
                "internal": {
                    "url": "https://internal.example.test/mcp",
                    "headers": {"Authorization": "Bearer ${TOKEN}"},
                }
            }
        },
    )
    _write_json(project / ".pi" / "mcp.json", {"mcpServers": {"internal": {"enabled": False}}})

    servers = _servers(_installed(tmp_path, project))

    assert servers["internal"].extra["enabled"] is False
    assert servers["internal"].source_manifest == str(agent / "mcp.json")


def test_without_a_project_only_the_user_file_is_read(tmp_path):
    agent = _agent_dir(tmp_path)
    _write_json(agent / "mcp.json", {"mcpServers": {"user": {"command": "u"}}})

    assert set(_servers(_installed(tmp_path))) == {"user"}


# --- Installed: trust ------------------------------------------------------------


def _project_with_server(tmp_path: Path) -> Path:
    project = tmp_path / "work" / "project"
    _write_json(project / ".pi" / "mcp.json", {"mcpServers": {"proj": {"command": "p"}}})
    return project


def test_a_project_with_no_trust_decision_is_composed_with_a_note(tmp_path):
    _agent_dir(tmp_path)
    warnings = WarningLog()
    project = _project_with_server(tmp_path)

    assert set(_servers(_installed(tmp_path, project, warnings))) == {"proj"}
    assert any("no trust decision" in w for w in warnings)
    assert not warnings.gaps


def test_a_distrusted_project_is_not_composed(tmp_path):
    agent = _agent_dir(tmp_path)
    project = _project_with_server(tmp_path)
    _write_json(agent / "trust.json", {str(project.resolve()): False})

    assert _servers(_installed(tmp_path, project)) == {}


def test_an_ancestor_decision_applies_and_null_is_no_decision(tmp_path):
    agent = _agent_dir(tmp_path)
    project = _project_with_server(tmp_path)
    _write_json(
        agent / "trust.json",
        {str(project.resolve()): None, str(project.resolve().parent): False},
    )

    assert _servers(_installed(tmp_path, project)) == {}


def test_a_never_default_skips_an_undecided_project(tmp_path):
    agent = _agent_dir(tmp_path)
    project = _project_with_server(tmp_path)
    _write_json(agent / "settings.json", {"defaultProjectTrust": "never"})

    assert _servers(_installed(tmp_path, project)) == {}

    _write_json(agent / "trust.json", {str(project.resolve()): True})
    assert set(_servers(_installed(tmp_path, project))) == {"proj"}


def test_a_malformed_trust_store_is_a_gap(tmp_path):
    agent = _agent_dir(tmp_path)
    warnings = WarningLog()
    project = _project_with_server(tmp_path)
    _write_json(agent / "trust.json", {str(project.resolve()): "yes"})

    _installed(tmp_path, project, warnings)

    assert len(warnings.gaps) == 1


# --- Installed: skills and keys -----------------------------------------------------


def test_project_agents_skills_are_read_up_to_the_repository_root(tmp_path):
    _agent_dir(tmp_path)
    repo = tmp_path / "home" / "repo"
    (repo / ".git").mkdir(parents=True)
    project = repo / "packages" / "app"
    project.mkdir(parents=True)
    _skill(project / ".agents" / "skills" / "app-skill")
    _skill(repo / ".agents" / "skills" / "repo-skill")
    _skill(tmp_path / "home" / ".agents" / "skills" / "user-skill")
    _skill(tmp_path / ".agents" / "skills" / "above-repo")

    graph = _installed(tmp_path, project)

    assert _names(graph, "skill") == ["app-skill", "repo-skill", "user-skill"]
    keys = sorted(n.key for n in graph.nodes.values() if n.kind == "skill")
    assert keys == [
        "agents/skills/user-skill/SKILL.md#$.frontmatter#user-skill",
        "project/.agents/skills/repo-skill/SKILL.md#$.frontmatter#repo-skill",
        "project/packages/app/.agents/skills/app-skill/SKILL.md#$.frontmatter#app-skill",
    ]


def test_installed_keys_carry_no_home_path(tmp_path):
    agent = _agent_dir(tmp_path)
    _write_json(agent / "mcp.json", {"mcpServers": {"a": {"command": "a"}}})
    _write(agent / "extensions" / "gate.ts")
    _skill(agent / "skills" / "deploy")
    _write(agent / "prompts" / "review.md", "Review.\n")
    _write_json(agent / "settings.json", {"packages": ["npm:tools@1.0.0"]})
    _write_json(
        agent / "npm" / "node_modules" / "tools" / "package.json",
        {"name": "tools", "version": "1.0.0"},
    )

    graph = _installed(tmp_path)

    keys = [n.key for n in graph.nodes.values() if n.ref is not None]
    assert len(keys) == 5
    assert all(key.startswith("pi/") for key in keys), keys
    assert not [key for key in keys if str(tmp_path) in key]


# --- Installed: packages ---------------------------------------------------------


def _npm_package(agent: Path, name: str, version: str, pi: dict | None = None) -> Path:
    root = agent / "npm" / "node_modules" / name
    data: dict = {"name": name, "version": version}
    if pi is not None:
        data["pi"] = pi
    _write_json(root / "package.json", data)
    return root


def test_an_npm_package_carries_its_installed_version_and_contents(tmp_path):
    agent = _agent_dir(tmp_path)
    _write_json(agent / "settings.json", {"packages": ["npm:@scope/tools@^1.0.0"]})
    root = _npm_package(
        agent, "@scope/tools", "1.4.2", {"extensions": ["./ext.ts"], "skills": ["./skills"]}
    )
    _write(root / "ext.ts")
    _skill(root / "skills" / "review")
    _write(root / "prompts" / "ignored.md", "Not listed by the manifest.\n")

    graph = _installed(tmp_path)

    (package,) = [r for r in _refs(graph, "plugin") if r.name == "@scope/tools"]
    assert package.purl == "pkg:npm/%40scope/tools@1.4.2"
    assert package.component_identity == "plugin/npm/@scope/tools"
    assert package.extra["install_source"] == "npm:@scope/tools@^1.0.0"
    assert package.extra["scope"] == "user"
    assert package.source_locator == "$.packages[0]"
    children = {r.name: r for r in _children(graph, package)}
    assert set(children) == {"ext", "review"}
    assert children["review"].component_identity == "skill/plugin/npm/@scope/tools/review"
    assert children["ext"].extra["declared_by"]["kind"] == "plugin"


def test_a_package_without_a_manifest_uses_its_conventional_directories(tmp_path):
    agent = _agent_dir(tmp_path)
    _write_json(agent / "settings.json", {"packages": ["npm:tools"]})
    root = _npm_package(agent, "tools", "2.0.0")
    _write(root / "extensions" / "gate.ts")
    _write(root / "prompts" / "deep" / "ship.md", "Ship.\n")
    _skill(root / "skills" / "lint")

    graph = _installed(tmp_path)

    (package,) = [r for r in _refs(graph, "plugin") if r.name == "tools"]
    assert sorted(r.name for r in _children(graph, package)) == ["gate", "lint", "ship"]


def test_a_manifest_glob_skips_dot_segments(tmp_path):
    agent = _agent_dir(tmp_path)
    _write_json(agent / "settings.json", {"packages": ["npm:tools@1.0.0"]})
    root = _npm_package(agent, "tools", "1.0.0", {"prompts": ["./resources/**/*.md"]})
    _write(root / "resources" / "ship.md", "Ship.\n")
    _write(root / "resources" / ".private" / "hidden.md", "Hidden.\n")

    assert _names(_installed(tmp_path), "command") == ["ship"]


def test_an_npm_package_that_is_not_installed_is_presence_only_with_a_gap(tmp_path):
    agent = _agent_dir(tmp_path)
    warnings = WarningLog()
    _write_json(agent / "settings.json", {"packages": ["npm:tools@3.1.0"]})

    graph = _installed(tmp_path, warnings=warnings)

    (package,) = _refs(graph, "plugin")
    assert package.purl == "pkg:npm/tools@3.1.0"
    assert _children(graph, package) == []
    assert len(warnings.gaps) == 1
    assert "global npm install" in warnings.gaps[0]


def test_a_git_package_carries_its_checkout_commit(tmp_path):
    agent = _agent_dir(tmp_path)
    commit = "0123456789abcdef0123456789abcdef01234567"
    _write_json(
        agent / "settings.json",
        {
            "packages": [
                "git:github.com/Owner/repo@v1",
                "https://gitlab.example.test/group/tools",
                # An SCP-style address needs the `git:` prefix; without it Pi
                # reads a local path.
                "git:git@github.com:owner/pinned.git@" + "f" * 40,
            ]
        },
    )
    checkout = agent / "git" / "github.com" / "Owner" / "repo"
    _write(checkout / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(checkout / ".git" / "packed-refs", f"# pack\n{commit} refs/heads/main\n")
    _write(checkout / "prompts" / "ship.md", "Ship.\n")
    gitlab = agent / "git" / "gitlab.example.test" / "group" / "tools"
    _write(gitlab / ".git" / "HEAD", commit.upper() + "\n")

    graph = _installed(tmp_path)

    packages = {r.component_identity: r for r in _refs(graph, "plugin")}
    github = packages["plugin/git/github.com/Owner/repo"]
    assert github.purl == f"pkg:github/Owner/repo@{commit}"
    assert github.extra["git_ref"] == "v1"
    assert github.extra["install_source"] == "git+https://github.com/Owner/repo@v1"
    other = packages["plugin/git/gitlab.example.test/group/tools"]
    assert (other.ecosystem, other.name, other.version) == (
        None,
        "gitlab.example.test/group/tools",
        commit,
    )
    pinned = packages["plugin/git/github.com/owner/pinned"]
    assert pinned.version == "f" * 40
    assert "git_ref" not in pinned.extra


def test_a_project_package_replaces_the_user_one(tmp_path):
    agent = _agent_dir(tmp_path)
    project = tmp_path / "project"
    _write_json(agent / "settings.json", {"packages": ["npm:tools@1.0.0"]})
    _write_json(project / ".pi" / "settings.json", {"packages": ["npm:tools@2.0.0"]})
    _write_json(
        project / ".pi" / "npm" / "node_modules" / "tools" / "package.json",
        {"name": "tools", "version": "2.0.0"},
    )

    (package,) = _refs(_installed(tmp_path, project), "plugin")

    assert (package.version, package.extra["scope"]) == ("2.0.0", "project")


def test_a_project_autoload_delta_leaves_the_user_package(tmp_path):
    agent = _agent_dir(tmp_path)
    project = tmp_path / "project"
    _write_json(agent / "settings.json", {"packages": ["npm:tools@1.0.0"]})
    _npm_package(agent, "tools", "1.0.0")
    _write_json(
        project / ".pi" / "settings.json",
        {"packages": [{"source": "npm:tools", "autoload": False, "skills": []}]},
    )

    (package,) = _refs(_installed(tmp_path, project), "plugin")

    assert (package.version, package.extra["scope"]) == ("1.0.0", "user")


def test_a_local_package_file_is_one_extension(tmp_path):
    agent = _agent_dir(tmp_path)
    _write_json(agent / "settings.json", {"packages": ["./local/ext.ts", "./missing"]})
    _write(agent / "local" / "ext.ts")

    graph = _installed(tmp_path)

    assert _names(graph, "plugin") == ["ext"]


def test_a_resource_reached_twice_is_composed_once(tmp_path):
    agent = _agent_dir(tmp_path)
    _write_json(agent / "settings.json", {"skills": ["skills/deploy"]})
    _skill(agent / "skills" / "deploy")

    assert _names(_installed(tmp_path), "skill") == ["deploy"]
