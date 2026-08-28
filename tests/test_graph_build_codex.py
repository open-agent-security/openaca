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
import os
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


def test_malformed_inline_hooks_table_is_a_gap_not_a_silent_drop(tmp_path):
    """`hooks = "bad"` is valid TOML but the wrong shape (a string, not a
    table). `codex_config.load_config` must not coerce this to `{}` — that
    coercion would make it indistinguishable from "no hooks declared" and
    `hooks_json.parse_settings_hooks(strict=True)` would never get a chance
    to reject it."""
    root = _repo(tmp_path)
    (root / ".codex" / "config.toml").write_text(
        'hooks = "bad"\n\n[mcp_servers.svc]\ncommand = "npx"\nargs = ["@scope/tool@1.2.3"]\n',
        encoding="utf-8",
    )
    warnings: list[str] = []

    graph = build_codex_declared_graph(_agent(root), warnings=warnings)

    assert "skill" in _kinds(graph), "the rest of the tree still composes"
    assert any("config.toml" in w and "hooks" in w for w in graph.warnings + warnings), (
        "a malformed hooks table must be a recorded gap, not a silent drop"
    )


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


def _trust(root: Path, project: Path) -> None:
    """Record `project` as trusted in the endpoint's base config.

    Codex ignores a project's `.codex/config.toml` until the directory is
    trusted, so a fixture that wants the project layer composed has to say so.
    """
    root.joinpath("config.toml").write_text(
        root.joinpath("config.toml").read_text(encoding="utf-8")
        + f'\n[projects."{project.resolve()}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )


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

    _trust(root, project)
    graph = build_codex_installed_graph(root, project)
    skill_names = _skill_names(graph)
    user_svc = _server_named(graph, "user_svc")

    assert "proj-skill" in skill_names
    assert len(user_svc) == 1, "project entry replaces the user one, not both"
    assert str(project) in user_svc[0].source_manifest


def test_a_nested_project_skill_is_composed(tmp_path):
    """`_seed_shared_endpoint_surfaces` now threads Codex's own `RepoSurface`
    into `_add_project_skills`, which — like `graph_build_cursor`'s any-depth
    walk — recognises `.codex/skills/**/SKILL.md` at any nesting depth, not
    just one level down. Before this, only the direct-child supplementary pass
    (`_add_skills_from_dir`) found project skills at the endpoint, so a skill
    nested a level deeper was invisible."""
    root = _home(tmp_path)
    project = tmp_path / "nested-skill-proj"
    (project / ".codex" / "skills" / "team" / "nested-skill").mkdir(parents=True)
    (project / ".codex" / "skills" / "team" / "nested-skill" / "SKILL.md").write_text(
        "---\nname: nested-skill\n---\nN\n", encoding="utf-8"
    )

    graph = build_codex_installed_graph(root, project)

    assert "nested-skill" in _skill_names(graph)


def test_a_project_skill_symlinked_below_a_nested_directory_is_composed(tmp_path):
    """A symlink one level below the top of `.codex/skills/`
    (`skills/team/aws-api -> /store/aws-api`) must also be discovered.

    `_add_project_skills`'s own walk (`iter_unignored_files`, os.walk-based)
    never follows directory symlinks at any depth, and the flat
    `_add_skills_from_dir` symlink patch only resolves `skills_dir`'s
    immediate children — so a symlink nested a level deeper was missed by
    both. `_seed_shared_endpoint_surfaces` now routes a `RepoSurface` with
    `skill_config_dirs` set (Codex) through the cycle-safe recursive walker
    (`_add_project_skills_from_dir_following_symlinks`) instead.
    """
    root = _home(tmp_path)
    project = tmp_path / "symlinked-skill-proj"

    real_skill_dir = tmp_path / "skills-store" / "aws-api"
    real_skill_dir.mkdir(parents=True)
    (real_skill_dir / "SKILL.md").write_text("---\nname: aws-api\n---\nrun\n", encoding="utf-8")

    team_dir = project / ".codex" / "skills" / "team"
    team_dir.mkdir(parents=True)
    os.symlink(real_skill_dir, team_dir / "aws-api")

    graph = build_codex_installed_graph(root, project)

    assert "aws-api" in _skill_names(graph)


def test_a_claude_only_project_skill_is_not_composed_into_the_codex_bom(tmp_path):
    """`_add_project_skills`'s `.claude`-vs-`.codex` skill-directory match is a
    `RepoSurface` field, not an `EndpointSurface` one. Before threading Codex's
    `RepoSurface` through, this call defaulted to `CLAUDE_CODE_SURFACE`
    regardless of which kind was scanning, so a project containing a
    `.claude/skills/` tree (but no matching `.codex/skills/` entry) leaked the
    Claude-only skill into the Codex BOM."""
    root = _home(tmp_path)
    project = tmp_path / "mixed-proj"
    (project / ".claude" / "skills" / "claude-only-skill").mkdir(parents=True)
    (project / ".claude" / "skills" / "claude-only-skill" / "SKILL.md").write_text(
        "---\nname: claude-only-skill\n---\nC\n", encoding="utf-8"
    )

    graph = build_codex_installed_graph(root, project)

    assert "claude-only-skill" not in _skill_names(graph)


def test_a_project_trusted_only_by_a_profile_does_not_override_a_base_mcp_server(tmp_path):
    """Mirrors the profile-only-trust plugin fix
    (`test_a_project_trusted_only_by_a_profile_does_not_override_a_base_enable`):
    a project trust record that exists only in a profile's
    `<name>.config.toml` is in effect only while that profile is selected, not
    on every invocation. The base config declares `user_svc`; the project
    (trusted only via a profile) redeclares it with a different command. A
    no-profile invocation never loads the project layer, so the base's
    `user_svc` must stay reachable rather than being replaced — the project's
    redeclaration joins the additive union instead, the same as a profile's
    own server would."""
    root = _home(tmp_path)
    project = tmp_path / "profile-trusted-mcp-proj"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[mcp_servers.user_svc]\ncommand = "project-wins"\n', encoding="utf-8"
    )
    (root / "work.config.toml").write_text(
        f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root, project)
    user_svc = _server_named(graph, "user_svc")

    assert len(user_svc) == 2, "the base occurrence stays reachable alongside the project's"
    sources = {ref.source_manifest for ref in user_svc}
    assert any(str(root / "config.toml") == source for source in sources)
    assert any(str(project) in source for source in sources)


def test_an_untrusted_projects_hooks_json_is_not_composed_at_the_endpoint(tmp_path):
    """`.codex/hooks.json` is trust-gated at the endpoint, the same as the
    project's `.codex/config.toml` layer — an untrusted project's sidecar must
    not compose."""
    root = _home(tmp_path)
    project = tmp_path / "proj2"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")

    graph = build_codex_installed_graph(root, project)

    assert not any(n.kind == "hook" for n in graph.nodes.values())


def test_the_user_roots_hooks_json_is_composed_at_the_endpoint(tmp_path):
    """`$CODEX_HOME/hooks.json` is a documented sidecar distinct from the
    inline `[hooks]` config.toml table; endpoint mode previously read only the
    inline form, silently dropping hooks declared this way."""
    root = _home(tmp_path)
    (root / "hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")

    graph = build_codex_installed_graph(root)
    hooks = _refs(graph, "hook")

    assert len(hooks) == 1
    assert hooks[0].extra["command"] == "echo hi"
    assert hooks[0].extra["scope"] == "user"
    assert str(root / "hooks.json") in hooks[0].source_manifest


def test_a_trusted_projects_hooks_json_is_composed_at_the_endpoint(tmp_path):
    """The project counterpart of the user-root sidecar above: a trusted
    project's `.codex/hooks.json` must compose with project scope."""
    root = _home(tmp_path)
    project = tmp_path / "proj4"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")
    _trust(root, project)

    graph = build_codex_installed_graph(root, project)
    hooks = _refs(graph, "hook")

    assert len(hooks) == 1
    assert hooks[0].extra["command"] == "echo hi"
    assert hooks[0].extra["scope"] == "project"
    assert str(project / ".codex" / "hooks.json") in hooks[0].source_manifest


def test_hooks_declared_inline_in_config_toml_are_composed_at_the_endpoint(tmp_path):
    """`$CODEX_HOME/config.toml` can carry hooks as an inline `[hooks]` table
    (documented alternative to the sidecar `hooks.json`); a scan that only
    ever read `hooks.json` silently dropped every hook declared this way."""
    root = _home(tmp_path)
    with (root / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            "\n[[hooks.SessionStart]]\n"
            'matcher = "*"\n\n'
            "[[hooks.SessionStart.hooks]]\n"
            'type = "command"\n'
            'command = "echo hi"\n'
        )

    graph = build_codex_installed_graph(root)
    hooks = _refs(graph, "hook")

    assert len(hooks) == 1
    assert hooks[0].extra["event"] == "SessionStart"
    assert hooks[0].extra["type"] == "command"
    assert hooks[0].extra["command"] == "echo hi"
    assert str(root / "config.toml") in hooks[0].source_manifest


def test_malformed_inline_hooks_table_is_a_gap_at_the_endpoint(tmp_path):
    """Same coercion bug as the declared-mode case, exercised through the
    installed-endpoint reader (`_seed_codex_hooks_from_layer`), which shares
    `codex_config.load_config` with the declared-mode one.

    Uses a profile layer (`work.config.toml`) rather than appending to the
    base `config.toml`: TOML has no way to add a bare top-level key after a
    `[table]` header without it becoming a member of that table, so a fresh
    file is the only way to isolate a top-level `hooks = "bad"`."""
    root = _home(tmp_path)
    (root / "work.config.toml").write_text('hooks = "bad"\n', encoding="utf-8")
    warnings: list[str] = []

    graph = build_codex_installed_graph(root, warnings=warnings)

    assert not _refs(graph, "hook")
    assert any("work.config.toml" in w and "hooks" in w for w in graph.warnings + warnings), (
        "a malformed hooks table must be a recorded gap, not a silent drop"
    )


def test_hooks_in_a_profile_config_are_composed_at_the_endpoint(tmp_path):
    """`codex -p work` layers `<root>/work.config.toml` over the base config
    (verified for MCP servers by `_seed_codex_profile_mcp_servers`; hooks fire
    from every active layer, not just the base one, so a profile's own
    `[hooks]` table must compose too, not silently drop)."""
    root = _home(tmp_path)
    (root / "work.config.toml").write_text(
        "[[hooks.SessionStart]]\n[[hooks.SessionStart.hooks]]\n"
        'type = "command"\ncommand = "echo profile"\n',
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)
    hooks = _refs(graph, "hook")

    assert len(hooks) == 1
    assert hooks[0].extra["command"] == "echo profile"
    assert str(root / "work.config.toml") in hooks[0].source_manifest


def test_hooks_in_a_trusted_project_config_are_composed_at_the_endpoint(tmp_path):
    """A trusted project's `.codex/config.toml` contributes hooks with project
    scope, the same layer its MCP servers come from."""
    root = _home(tmp_path)
    project = tmp_path / "proj3"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        "[[hooks.SessionStart]]\n[[hooks.SessionStart.hooks]]\n"
        'type = "command"\ncommand = "echo project"\n',
        encoding="utf-8",
    )
    _trust(root, project)

    graph = build_codex_installed_graph(root, project)
    hooks = _refs(graph, "hook")

    assert len(hooks) == 1
    assert hooks[0].extra["command"] == "echo project"
    assert hooks[0].extra["scope"] == "project"
    assert str(project / ".codex" / "config.toml") in hooks[0].source_manifest


def test_hooks_declared_inline_in_config_toml_are_composed_in_repo_mode(tmp_path):
    """The declared-graph counterpart of the endpoint test above: a project
    declaring hooks only via `.codex/config.toml`'s `[hooks]` table, never a
    sidecar `hooks.json`, must not have them silently absent from the BOM."""
    root = _repo(tmp_path)
    with (root / ".codex" / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            "\n[[hooks.SessionStart]]\n"
            "[[hooks.SessionStart.hooks]]\n"
            'type = "command"\n'
            'command = "echo repo"\n'
        )

    graph = build_codex_declared_graph(_agent(root))
    hooks = [n.ref for n in graph.nodes.values() if n.kind == "hook" and n.ref is not None]
    inline = [r for r in hooks if r.extra["command"] == "echo repo"]

    assert len(inline) == 1
    assert inline[0].extra["scope"] == "project"
    assert str(root / ".codex" / "config.toml") in inline[0].source_manifest


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

    _trust(root, project)
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


def test_a_plugin_enabled_only_in_a_profile_is_reported_enabled(tmp_path):
    """`--profile` layers `<root>/<name>.config.toml` over the base for
    `[plugins.*]` too, not just `[mcp_servers.*]`. The base config disables
    `offpl@mkt`; a profile flips it on, and which profile is selected leaves
    no trace on disk, so the union — the same over-approximating direction
    used for profile MCP servers — reports it enabled."""
    root = _home(tmp_path)
    (root / "work.config.toml").write_text(
        '[plugins."offpl@mkt"]\nenabled = true\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)

    assert _plugins(graph)["offpl"].extra["enabled"] is True


def test_a_plugin_disabled_only_in_a_profile_stays_enabled(tmp_path):
    """The base config enables `codexpl@mkt`; a profile disabling it must not
    downgrade the union, for the same reason the reverse direction enables."""
    root = _home(tmp_path)
    (root / "work.config.toml").write_text(
        '[plugins."codexpl@mkt"]\nenabled = false\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)

    assert _plugins(graph)["codexpl"].extra["enabled"] is True


def test_a_plugin_enabled_only_in_a_trusted_project_is_reported_enabled(tmp_path):
    """`[plugins.*]`/`[marketplaces.*]` are ordinary tables in any config
    layer `codex_config_layers` returns, not a base/profile-only surface — a
    trusted project's `.codex/config.toml` flips the base config's disabled
    `offpl@mkt` on, the same as a profile does."""
    root = _home(tmp_path)
    project = tmp_path / "trusted-plugin-proj"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[plugins."offpl@mkt"]\nenabled = true\n', encoding="utf-8"
    )
    _trust(root, project)

    graph = build_codex_installed_graph(root, project)

    assert _plugins(graph)["offpl"].extra["enabled"] is True


def test_a_plugin_disabled_only_in_a_trusted_project_overrides_the_base_enable(tmp_path):
    """Unlike a profile (an alternate Codex may or may not have selected), a
    trusted project's `.codex/config.toml` is unconditionally active once
    trusted — it is a higher-precedence layer, not another union member.
    The base config enables `codexpl@mkt`; the project's explicit `false`
    must win, matching Codex's documented project-overrides-user config
    precedence and the same "project entries win" rule
    `_seed_codex_mcp_servers` already applies to servers."""
    root = _home(tmp_path)
    project = tmp_path / "trusted-plugin-proj"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[plugins."codexpl@mkt"]\nenabled = false\n', encoding="utf-8"
    )
    _trust(root, project)

    graph = build_codex_installed_graph(root, project)

    assert _plugins(graph)["codexpl"].extra["enabled"] is False


def test_a_project_trusted_only_by_a_profile_does_not_override_a_base_enable(tmp_path):
    """A project trust record that exists only in a profile's
    `<name>.config.toml` is in effect only while that profile is selected —
    unlike a base trust record, it is not unconditionally active. The base
    config enables `codexpl@mkt`; a no-profile invocation never loads the
    project layer at all, so it stays reachable, and the project's explicit
    `false` must not erase it (it joins the same OR-union a profile's own
    disable already cannot win against)."""
    root = _home(tmp_path)
    project = tmp_path / "profile-trusted-plugin-proj"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[plugins."codexpl@mkt"]\nenabled = false\n', encoding="utf-8"
    )
    (root / "work.config.toml").write_text(
        f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root, project)

    assert _plugins(graph)["codexpl"].extra["enabled"] is True


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


def test_an_untrusted_project_layer_is_not_composed(tmp_path):
    """Codex ignores a project's config until the directory is trusted, so
    composing it unconditionally reports servers, hooks, and plugins the
    runtime does not load."""
    root = _home(tmp_path)
    project = tmp_path / "untrusted"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[mcp_servers.ghost]\ncommand = "true"\n\n'
        "[[hooks.SessionStart]]\n[[hooks.SessionStart.hooks]]\n"
        'type = "command"\ncommand = "echo ghost"\n\n'
        '[plugins."offpl@mkt"]\nenabled = true\n',
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root, project)

    assert _server_named(graph, "ghost") == []
    assert not any(
        "echo ghost" in str((r.extra or {}).get("command")) for r in _refs(graph, "hook")
    )
    assert _plugins(graph)["offpl"].extra["enabled"] is False, (
        "untrusted project must not flip the base config's disabled plugin on"
    )


def test_trust_recorded_only_in_a_profile_still_gates_the_project_in(tmp_path):
    """`codex -p <name>` layers a profile over the base, so trust declared in a
    profile is real trust — and which profile is selected leaves no trace."""
    root = _home(tmp_path)
    project = tmp_path / "proj-via-profile"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[mcp_servers.from_project]\ncommand = "true"\n', encoding="utf-8"
    )
    (root / "trusting.config.toml").write_text(
        f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root, project)

    assert _server_named(graph, "from_project"), "profile-declared trust is trust"


def test_every_codex_surface_reads_one_layer_definition():
    """The layer set was the thing duplicated across surfaces, so it is the
    thing that is shared. Each surface still owns its own merge semantics."""
    import inspect

    from tools import graph_build

    for fn in (
        graph_build._seed_codex_mcp_servers,
        graph_build._seed_codex_profile_mcp_servers,
        graph_build._seed_codex_hooks,
        graph_build._seed_cache_plugins,
    ):
        src = inspect.getsource(fn)
        assert "codex_config_layers(" in src, f"{fn.__name__} builds its own layer list"
        assert '"*.config.toml"' not in src, f"{fn.__name__} re-globs profiles"
