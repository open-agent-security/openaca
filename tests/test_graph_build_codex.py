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


def test_a_valid_config_toml_is_not_routed_through_the_json_settings_parser(tmp_path):
    """`.codex/config.toml` is TOML, read directly by `codex_config`.

    The tree walk's standalone-settings branch matches
    `<config_dir>/<settings_filename>` and feeds it to
    `claude_settings.parse`, which is JSON-only. `RepoSurface.settings_filename`
    must be `None` for Codex so that branch never fires for `config.toml` —
    otherwise a perfectly valid Codex repo's own config produces a bogus
    "could not parse" gap and `composition_coverage` wrongly degrades from
    `complete` to `partial`.
    """
    warnings: list[str] = []

    graph = build_codex_declared_graph(_agent(_repo(tmp_path)), warnings=warnings)

    assert graph.warnings.gaps == []
    assert not any("config.toml" in w for w in warnings)


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


def test_a_malformed_declared_mcp_entry_is_a_gap_not_a_silent_drop(tmp_path):
    """`[mcp_servers] bad = "oops"` is valid TOML but the wrong shape (a
    string, not a table). The declared-mode reader must parse strictly, the
    same as `_emit_codex_config_mcp_servers` already does for installed mode
    — otherwise this one remaining non-strict call site silently drops the
    malformed entry and coverage stays `complete` over a dropped server."""
    root = _repo(tmp_path)
    (root / ".codex" / "config.toml").write_text('[mcp_servers]\nbad = "oops"\n', encoding="utf-8")
    warnings: list[str] = []

    graph = build_codex_declared_graph(_agent(root), warnings=warnings)

    assert "mcp_server" not in _kinds(graph), "a malformed entry must not become a phantom server"
    assert any("config.toml" in w for w in graph.warnings + warnings), (
        "a malformed mcp_servers entry must be a recorded gap, not a silent drop"
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
    (root / "agents" / "probe.toml").write_text(
        'name = "probe"\ndescription = "d"\ndeveloper_instructions = "i"\n',
        encoding="utf-8",
    )

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


def test_an_unreadable_marketplace_cache_dir_is_a_gap_not_a_crash(tmp_path):
    """`_seed_cache_plugins` used to call `cache_root.iterdir()` (and the two
    directory levels beneath it) unguarded. A marketplace subtree left with
    restrictive permissions after a partial install must degrade to a
    coverage gap, matching `_iter_skill_subdirs_following_symlinks`'s own
    guarded walk, rather than raising `PermissionError` and aborting the
    whole endpoint scan.
    """
    root = _home(tmp_path)
    locked_dir = root / "plugins" / "cache" / "locked-mkt"
    locked_dir.mkdir(parents=True)
    os.chmod(locked_dir, 0o000)
    try:
        warnings: list[str] = []
        graph = build_codex_installed_graph(root, warnings=warnings)
    finally:
        os.chmod(locked_dir, 0o755)

    if os.getuid() != 0:
        assert any("could not list" in w and "locked-mkt" in w for w in warnings)
        # The readable bundles from `_home` must still be composed.
        assert set(_plugins(graph)) == {"codexpl", "claudepl", "offpl"}


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


def test_shared_home_skill_keys_by_label_not_absolute_path(tmp_path, monkeypatch):
    """`$HOME/.agents/skills` (`_seed_codex_shared_agent_skills`) sits outside
    both `config_root` and `project_root`, so the endpoint normalizer must
    carry it as a labeled `extra_roots` entry — as Cursor's own endpoint
    builder already does for its home-scoped compat roots — or the node key
    falls back to the machine-specific absolute path, breaking bom-ref
    stability and cross-machine dedup."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    (fake_home / ".agents" / "skills" / "shared-skill").mkdir(parents=True)
    (fake_home / ".agents" / "skills" / "shared-skill" / "SKILL.md").write_text(
        "---\nname: shared-skill\n---\nShared.\n", encoding="utf-8"
    )

    graph = build_codex_installed_graph(_home(tmp_path))

    shared = [
        n
        for n in graph.nodes.values()
        if n.kind == "skill" and n.ref and n.ref.name == "shared-skill"
    ]
    assert len(shared) == 1
    assert shared[0].key.startswith("agents/skills/shared-skill/SKILL.md#")


def test_the_shared_skills_root_relocates_with_config_dir(tmp_path):
    """ADR-0054 grants `--config-dir` only to a kind for which naming a root
    fully specifies the target. `$HOME/.agents/skills` is not moved by
    `$CODEX_HOME`, so the flag moves its companion instead: on a real endpoint
    `.codex` and `.agents` are siblings, so `<dir>/../.agents` is the faithful
    relocation. Skipping it under an override would ship a flag that knowingly
    returns an incomplete composition."""
    from tools.agent_kinds.codex import resolve_shared_skills_root

    home = tmp_path / "fake-home"
    home.mkdir()
    root = _home(home)  # <fake-home>/codex-home
    (home / ".agents" / "skills" / "relocated").mkdir(parents=True)
    (home / ".agents" / "skills" / "relocated" / "SKILL.md").write_text(
        "---\nname: relocated\n---\nS\n", encoding="utf-8"
    )

    graph = build_codex_installed_graph(root, shared_skills_root=resolve_shared_skills_root(root))

    assert "relocated" in _skill_names(graph)


def test_an_unoverridden_scan_reads_the_invoking_users_home(tmp_path):
    """Default and `$CODEX_HOME` both leave the shared root at the real home,
    because neither relocates it."""
    from pathlib import Path as _Path

    from tools.agent_kinds.codex import resolve_shared_skills_root

    assert resolve_shared_skills_root() == _Path.home() / ".agents"


def test_the_companion_root_is_a_sibling_of_the_named_root(tmp_path):
    from tools.agent_kinds.codex import resolve_shared_skills_root

    assert resolve_shared_skills_root(tmp_path / "somewhere" / "codex") == (
        tmp_path / "somewhere" / ".agents"
    )


def test_admin_skills_root_present_lowers_coverage(tmp_path, monkeypatch):
    """`/etc/codex/skills` is real per ADR-0058, not merely unaudited like
    `managed_config.toml` — when it exists on the scanned endpoint, its
    known-real skill components are known-missing from the graph, so the gap
    must be counted rather than silently absent."""
    from tools import graph_build

    admin_root = tmp_path / "etc-codex-skills"
    admin_root.mkdir()
    monkeypatch.setattr(graph_build, "_CODEX_ADMIN_SKILLS_ROOT", admin_root)

    graph = build_codex_installed_graph(_home(tmp_path))

    assert any("is not composed" in gap for gap in graph.warnings.gaps)


def test_admin_skills_root_absent_does_not_lower_coverage(tmp_path, monkeypatch):
    from tools import graph_build

    monkeypatch.setattr(
        graph_build, "_CODEX_ADMIN_SKILLS_ROOT", tmp_path / "no-such-etc-codex-skills"
    )

    graph = build_codex_installed_graph(_home(tmp_path))

    assert not any("is not composed" in gap for gap in graph.warnings.gaps)


def test_subagents_are_composed_from_toml(tmp_path):
    graph = build_codex_installed_graph(_home(tmp_path))
    agents = {r.name for r in _refs(graph, "agent")}

    assert agents == {"probe"}


def test_a_non_table_agent_role_is_not_a_phantom_subagent(tmp_path):
    """`agents.review = "bad"` must not read like a real, file-less role and
    be emitted as a subagent — it must gap instead."""
    root = _home(tmp_path)
    # A trailing bare `agents.review = "bad"` would attach to whatever table
    # `CONFIG` last opened (`[mcp_servers.user_svc]`) rather than declaring a
    # top-level `agents` table — `[agents]` resets the TOML table context.
    (root / "config.toml").write_text(CONFIG + '\n[agents]\nreview = "bad"\n', encoding="utf-8")
    warnings: list[str] = []

    graph = build_codex_installed_graph(root, warnings=warnings)
    agents = {r.name for r in _refs(graph, "agent")}

    assert agents == {"probe"}, "the malformed role must not appear as a subagent"
    assert any("agents.review" in w for w in graph.warnings.gaps + warnings)


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


def test_a_gitignored_project_skill_symlinked_below_a_nested_directory_is_excluded(tmp_path):
    """`_add_project_skills_from_dir_following_symlinks` (the recursive
    symlink-follow path used above for `.codex/skills/team/aws-api ->`) must
    still honor the project root's `.gitignore`, matching every other
    project-skill surface (`_seed_shared_endpoint_surfaces` loads
    `project_skill_spec` from the project root specifically because project
    skills — unlike installed-plugin/install-root skills — are filtered).
    Before threading `root_dir`/`root_spec` through this recursive pass, an
    ignored nested skill symlink still surfaced.
    """
    root = _home(tmp_path)
    project = tmp_path / "gitignored-skill-proj"

    real_skill_dir = tmp_path / "skills-store" / "aws-api"
    real_skill_dir.mkdir(parents=True)
    (real_skill_dir / "SKILL.md").write_text("---\nname: aws-api\n---\nrun\n", encoding="utf-8")

    team_dir = project / ".codex" / "skills" / "team"
    team_dir.mkdir(parents=True)
    os.symlink(real_skill_dir, team_dir / "aws-api")
    (project / ".gitignore").write_text(".codex/skills/team/\n", encoding="utf-8")

    graph = build_codex_installed_graph(root, project)

    assert "aws-api" not in _skill_names(graph)


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


def test_layer_activation_is_decided_once_not_per_surface(tmp_path):
    """The profile-only-trust condition lives on the layer.

    Each surface deriving its own answer is what produced a round of findings
    where MCP servers, cached plugins, and hooks each disagreed about the same
    endpoint. `codex_config_layers` decides; surfaces read `layer.overrides`.
    """
    import inspect

    from tools import graph_build

    for fn in (graph_build._seed_codex_mcp_servers, graph_build._seed_cache_plugins):
        src = inspect.getsource(fn)
        assert "layer.overrides" in src, f"{fn.__name__} should read the layer's condition"
        assert "codex_project_trusted_unconditionally(" not in src, (
            f"{fn.__name__} re-derives the activation condition"
        )


def test_a_profile_trusted_project_layer_does_not_override(tmp_path):
    """Trust declared only in a profile makes the project layer conditional: a
    base-declared server stays reachable through a plain, no-profile run, so
    the project must not replace it by name."""
    root = _home(tmp_path)
    project = tmp_path / "proj-cond"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[mcp_servers.user_svc]\ncommand = "project-version"\n', encoding="utf-8"
    )
    (root / "trusting.config.toml").write_text(
        f'[projects."{project.resolve()}"]\ntrust_level = "trusted"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root, project)

    assert len(_server_named(graph, "user_svc")) == 2, (
        "both the base and the conditionally-active project server are reachable"
    )


def test_an_unconditionally_trusted_project_layer_overrides(tmp_path):
    """Trust in the base config is active on every invocation, so the project
    layer is a higher-precedence override rather than an alternate."""
    root = _home(tmp_path)
    project = tmp_path / "proj-uncond"
    (project / ".codex").mkdir(parents=True)
    (project / ".codex" / "config.toml").write_text(
        '[mcp_servers.user_svc]\ncommand = "project-version"\n', encoding="utf-8"
    )
    _trust(root, project)

    graph = build_codex_installed_graph(root, project)
    svc = _server_named(graph, "user_svc")

    assert len(svc) == 1
    assert str(project) in svc[0].source_manifest


# --- Config-declared subagent roles (PR #178 review) -----------------------
#
# `[agents."<role>"] config_file = "..."` is a second declaration form. A role
# whose file sits outside `agents/` was previously reported as no subagent at
# all. Verified against developers.openai.com/codex/config-reference:
# "Path to a TOML config layer for that role; relative paths resolve from the
# config file that declares the role."


def test_a_config_declared_role_outside_the_agents_dir_is_composed(tmp_path):
    root = _home(tmp_path)
    (root / "roles").mkdir()
    (root / "roles" / "lens.toml").write_text(
        'name = "lens-file-name"\ndescription = "d"\ndeveloper_instructions = "i"\n',
        encoding="utf-8",
    )
    (root / "config.toml").write_text(
        CONFIG + '\n[agents.lens]\nconfig_file = "roles/lens.toml"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)

    assert "lens" in {r.name for r in _refs(graph, "agent")}


def test_the_table_key_is_the_role_identity_not_the_files_name(tmp_path):
    """The key selects the role, so the referenced file's own `name` is free to
    disagree and must not win."""
    root = _home(tmp_path)
    (root / "roles").mkdir()
    (root / "roles" / "lens.toml").write_text(
        'name = "something-else"\ndescription = "d"\ndeveloper_instructions = "i"\n',
        encoding="utf-8",
    )
    (root / "config.toml").write_text(
        CONFIG + '\n[agents.lens]\nconfig_file = "roles/lens.toml"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)
    names = {r.name for r in _refs(graph, "agent")}

    assert "lens" in names
    assert "something-else" not in names


def test_a_config_declared_roles_locator_matches_its_own_manifest(tmp_path):
    """`source_locator` must describe a path that exists inside
    `source_manifest`. The referenced `config_file` layer has no
    `agents.<role>` table of its own — it is read wholesale, so the locator
    must be `$` (the file's own root), the same convention `codex_agent.parse`
    uses for a standalone file — not the `$.agents."<role>"` path that only
    exists in the DECLARING config."""
    root = _home(tmp_path)
    (root / "roles").mkdir()
    (root / "roles" / "lens.toml").write_text(
        'description = "d"\ndeveloper_instructions = "i"\n', encoding="utf-8"
    )
    (root / "config.toml").write_text(
        CONFIG + '\n[agents.lens]\nconfig_file = "roles/lens.toml"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)
    (ref,) = [r for r in _refs(graph, "agent") if r.name == "lens"]

    assert ref.source_manifest == str(root / "roles" / "lens.toml")
    assert ref.source_locator == "$"


def test_an_out_of_root_config_file_anchors_to_the_declaring_config(tmp_path):
    """`config_file` may be an absolute path anywhere on disk ("Path to a TOML
    config layer for that role" — the reference names no root requirement).
    When it sits outside `config_root`, `project_root`, and the shared
    `.agents` root alike, anchoring `source_manifest` to it (as the in-root
    case above does) would leave a machine-specific absolute path in the
    occurrence key and CycloneDX bom-ref — there is no enumerable root left to
    label it under, unlike the in-root case. The occurrence instead anchors to
    `declaring_config`, which is always inside a known root."""
    root = _home(tmp_path)
    external = tmp_path / "external-roles"
    external.mkdir()
    (external / "lens.toml").write_text(
        'description = "d"\ndeveloper_instructions = "i"\n', encoding="utf-8"
    )
    (root / "config.toml").write_text(
        CONFIG + f'\n[agents.lens]\nconfig_file = "{(external / "lens.toml").as_posix()}"\n',
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)
    (ref,) = [r for r in _refs(graph, "agent") if r.name == "lens"]

    assert ref.source_manifest == str(root / "config.toml")
    assert ref.source_locator == '$.agents."lens".config_file'


def test_an_out_of_root_config_file_key_is_stable(tmp_path):
    """The occurrence key itself (what becomes the bom-ref) must not carry the
    external absolute path — that is the actual cross-machine instability the
    anchor-to-declaring-config fix exists to avoid."""
    from tools.graph_build import _make_normalizer, occurrence_key

    root = _home(tmp_path)
    external = tmp_path / "external-roles"
    external.mkdir()
    (external / "lens.toml").write_text(
        'description = "d"\ndeveloper_instructions = "i"\n', encoding="utf-8"
    )
    (root / "config.toml").write_text(
        CONFIG + f'\n[agents.lens]\nconfig_file = "{(external / "lens.toml").as_posix()}"\n',
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)
    (ref,) = [r for r in _refs(graph, "agent") if r.name == "lens"]
    normalize = _make_normalizer(
        "endpoint", root, root, None, "codex", extra_roots=(("agents", tmp_path / "unused"),)
    )

    assert str(external) not in occurrence_key(ref, normalize)


def test_an_in_root_config_file_still_dedupes_with_the_directory_form(tmp_path):
    """The in-root anchor-to-referenced-file behavior is deliberately kept
    (rather than always anchoring to `declaring_config`) because it is what
    lets a `config_file` pointing at a file already discovered directly under
    `<root>/agents/` collapse into the one node the directory-scan form
    already produced for it, instead of double-reporting the same role."""
    root = _home(tmp_path)
    (root / "agents" / "lens.toml").write_text(
        'name = "lens"\ndescription = "d"\ndeveloper_instructions = "i"\n', encoding="utf-8"
    )
    (root / "config.toml").write_text(
        CONFIG + '\n[agents.lens]\nconfig_file = "agents/lens.toml"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)

    assert len([r for r in _refs(graph, "agent") if r.name == "lens"]) == 1


def test_a_relative_config_file_resolves_from_the_declaring_config(tmp_path):
    """Not from the process cwd, and not from the config root — from the file
    that declares the role, which matters once profiles are involved."""
    root = _home(tmp_path)
    (root / "nested").mkdir()
    (root / "nested" / "role.toml").write_text(
        'name = "nested-role"\ndescription = "d"\ndeveloper_instructions = "i"\n',
        encoding="utf-8",
    )
    (root / "work.config.toml").write_text(
        '[agents.viewer]\nconfig_file = "nested/role.toml"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)

    assert "viewer" in {r.name for r in _refs(graph, "agent")}


def test_a_role_with_no_config_file_is_still_a_subagent(tmp_path):
    """The table alone declares the role; its instructions inherit from the
    parent session."""
    root = _home(tmp_path)
    (root / "config.toml").write_text(
        CONFIG + '\n[agents.inheritor]\ndescription = "inherits everything"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)
    roles = {r.name: r for r in _refs(graph, "agent")}

    assert "inheritor" in roles
    assert roles["inheritor"].extra["description"] == "inherits everything"


def test_a_missing_config_file_lowers_coverage(tmp_path):
    """The reference says the path "is validated at load time and must point to
    an existing file", so Codex treats this as an error — a component we know
    exists and cannot read."""
    from tools.graph import WarningLog
    from tools.scan import _component_gap_count

    root = _home(tmp_path)
    (root / "config.toml").write_text(
        CONFIG + '\n[agents.ghost]\nconfig_file = "nowhere/ghost.toml"\n', encoding="utf-8"
    )
    warnings = WarningLog()

    build_codex_installed_graph(root, warnings=warnings)

    assert _component_gap_count(warnings) >= 1
    assert any("config_file is unavailable" in w for w in warnings)


def test_directory_and_config_declared_roles_both_appear(tmp_path):
    """Both declaration forms are real; neither replaces the other."""
    root = _home(tmp_path)
    (root / "roles").mkdir()
    (root / "roles" / "viewer.toml").write_text(
        'name = "viewer"\ndescription = "d"\ndeveloper_instructions = "i"\n', encoding="utf-8"
    )
    (root / "config.toml").write_text(
        CONFIG + '\n[agents.viewer]\nconfig_file = "roles/viewer.toml"\n', encoding="utf-8"
    )

    graph = build_codex_installed_graph(root)
    names = {r.name for r in _refs(graph, "agent")}

    assert {"probe", "viewer"} <= names, "the agents-dir role and the config role"


def test_a_config_file_layer_missing_name_and_description_still_composes(tmp_path):
    """A `config_file` layer is documented as "a TOML config layer for that
    role", not the standalone-file "Custom agent file schema" — so a layer
    carrying only `developer_instructions` (the common case, since the role's
    name is the table key and its description already lives on the table) must
    not be rejected the way a standalone `agents/*.toml` file missing those
    fields is."""
    root = _home(tmp_path)
    (root / "roles").mkdir()
    (root / "roles" / "lens.toml").write_text('developer_instructions = "i"\n', encoding="utf-8")
    (root / "config.toml").write_text(
        CONFIG + '\n[agents.lens]\ndescription = "table description"\n'
        'config_file = "roles/lens.toml"\n',
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)
    roles = {r.name: r for r in _refs(graph, "agent")}

    assert "lens" in roles
    assert roles["lens"].extra["description"] == "table description"


def test_repo_agents_skills_compose_for_codex(tmp_path):
    """Codex's skills reference lists repository `.agents/skills`, walked from
    the working directory up to the repository root."""
    root = _repo(tmp_path)
    (root / ".agents" / "skills" / "shared").mkdir(parents=True)
    (root / ".agents" / "skills" / "shared" / "SKILL.md").write_text(
        "---\nname: shared\n---\nS\n", encoding="utf-8"
    )

    graph = build_codex_declared_graph(_agent(root))

    assert "shared" in _skill_names(graph)


def test_a_config_declared_role_is_composed_in_repo_mode(tmp_path):
    """A repo declaring a subagent only through `.codex/config.toml`'s
    `[agents."<role>"]` table must not have it silently absent from the
    declared BOM.

    Repo mode already reads two of that file's tables (`[mcp_servers]`,
    `[hooks]`); `[agents]` is the third component-declaring one, and it was
    read at the endpoint only — so a role checked into a repository, the
    place a config-declared role is most likely to be shared, went
    uninventoried.
    """
    root = _repo(tmp_path)
    (root / ".codex" / "roles").mkdir(parents=True)
    (root / ".codex" / "roles" / "reviewer.toml").write_text(
        'description = "reviews diffs"\n', encoding="utf-8"
    )
    with (root / ".codex" / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('\n[agents."reviewer"]\nconfig_file = "roles/reviewer.toml"\n')

    graph = build_codex_declared_graph(_agent(root))
    roles = {r.name: r for r in _refs(graph, "agent")}

    assert "reviewer" in roles
    assert roles["reviewer"].extra["description"] == "reviews diffs"


def test_a_config_declared_role_with_no_file_is_composed_in_repo_mode(tmp_path):
    root = _repo(tmp_path)
    with (root / ".codex" / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('\n[agents."solo"]\ndescription = "no layer file"\n')

    graph = build_codex_declared_graph(_agent(root))

    assert "solo" in {r.name for r in _refs(graph, "agent")}


def test_a_missing_config_file_lowers_coverage_in_repo_mode(tmp_path):
    root = _repo(tmp_path)
    with (root / ".codex" / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write('\n[agents."ghost"]\nconfig_file = "roles/absent.toml"\n')

    graph = build_codex_declared_graph(_agent(root))

    assert any("absent.toml" in gap for gap in graph.warnings.gaps)


def test_a_plugin_owned_config_declared_role_is_not_a_repo_role(tmp_path):
    """Plugin-owned content belongs to the plugin branch, the same exclusion
    the sibling MCP and hook walks over this file apply."""
    root = _repo(tmp_path)
    bundle = root / "bundles" / "demo"
    (bundle / ".codex-plugin").mkdir(parents=True)
    (bundle / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo"}), encoding="utf-8"
    )
    (bundle / "examples" / ".codex").mkdir(parents=True)
    (bundle / "examples" / ".codex" / "config.toml").write_text(
        '[agents."fixture"]\ndescription = "example only"\n', encoding="utf-8"
    )

    graph = build_codex_declared_graph(_agent(root))

    assert "fixture" not in {r.name for r in _refs(graph, "agent")}


def test_a_malformed_config_surface_is_gapped_once_in_repo_mode(tmp_path):
    """One walk over `.codex/config.toml`, so a malformed table records one
    gap — not one per surface reading the same file."""
    root = _repo(tmp_path)
    # First line, not appended: a bare key after a table header would land
    # inside that table rather than at the top level.
    (root / ".codex" / "config.toml").write_text(
        'agents = "bad"\n[mcp_servers.svc]\ncommand = "npx"\nargs = ["@scope/tool@1.2.3"]\n',
        encoding="utf-8",
    )

    graph = build_codex_declared_graph(_agent(root))
    matching = [g for g in graph.warnings.gaps if "agents must be a table" in g]

    assert len(matching) == 1


def test_a_project_agents_skill_symlinked_below_a_nested_directory_is_composed(tmp_path):
    """The `.agents/skills` counterpart of the `.codex/skills` symlink test.

    Codex reads both project skill roots, and `_add_project_skills`'s os.walk
    never follows directory symlinks — so the symlink patch has to cover every
    directory in `skill_config_dirs`, not only the endpoint's own
    `project_config_dir`.
    """
    root = _home(tmp_path)
    project = tmp_path / "shared-symlink-proj"

    real_skill_dir = tmp_path / "shared-store" / "gcp-api"
    real_skill_dir.mkdir(parents=True)
    (real_skill_dir / "SKILL.md").write_text("---\nname: gcp-api\n---\nrun\n", encoding="utf-8")

    team_dir = project / ".agents" / "skills" / "team"
    team_dir.mkdir(parents=True)
    os.symlink(real_skill_dir, team_dir / "gcp-api")

    graph = build_codex_installed_graph(root, project)

    assert "gcp-api" in _skill_names(graph)


def _cache_bundle(root, marketplace: str, name: str, version: str = "1.0.0"):
    d = root / "plugins" / "cache" / marketplace / name / version
    (d / ".codex-plugin").mkdir(parents=True)
    (d / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    (d / "skills" / "packaged").mkdir(parents=True)
    (d / "skills" / "packaged" / "SKILL.md").write_text(
        "---\nname: packaged\n---\nS\n", encoding="utf-8"
    )
    return d


def _marketplace_manifest(root, rel: str, name: str):
    path = root / ".tmp" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "plugins": []}), encoding="utf-8")
    return path


def test_a_marketplace_declared_only_by_its_manifest_grants_identity(tmp_path):
    """Codex composes marketplaces it never writes to `[marketplaces.*]`:
    `codex plugin marketplace list` reports one rooted at
    `$CODEX_HOME/.tmp/plugins` that config.toml does not declare. Without it,
    every plugin installed from that marketplace — and every skill, command,
    agent and hook inside it, which inherit the plugin's identity — has no
    cross-BOM join key."""
    root = _home(tmp_path)
    _cache_bundle(root, "openai-curated", "widgets")
    _marketplace_manifest(root, "plugins/.agents/plugins/marketplace.json", "openai-curated")

    graph = build_codex_installed_graph(root)
    plugin = next(r for r in _refs(graph, "plugin") if r.name == "widgets")
    skill = next(r for r in _refs(graph, "skill") if r.name == "packaged")

    assert plugin.component_identity == "plugin/openai-curated/widgets"
    assert skill.component_identity == "skill/plugin/openai-curated/widgets/packaged"


def test_a_marketplace_manifest_one_level_deeper_is_also_found(tmp_path):
    """`.tmp/bundled-marketplaces/<name>/` is the other observed root shape."""
    root = _home(tmp_path)
    _cache_bundle(root, "openai-bundled", "sites")
    _marketplace_manifest(
        root,
        "bundled-marketplaces/openai-bundled/.agents/plugins/marketplace.json",
        "openai-bundled",
    )

    graph = build_codex_installed_graph(root)
    plugin = next(r for r in _refs(graph, "plugin") if r.name == "sites")

    assert plugin.component_identity == "plugin/openai-bundled/sites"


def test_a_claude_shaped_marketplace_manifest_is_read_too(tmp_path):
    """Codex accepts both marketplace formats, so both name their own root."""
    root = _home(tmp_path)
    _cache_bundle(root, "vendor-mkt", "thing")
    _marketplace_manifest(
        root, "marketplaces/vendor-mkt/.claude-plugin/marketplace.json", "vendor-mkt"
    )

    graph = build_codex_installed_graph(root)
    plugin = next(r for r in _refs(graph, "plugin") if r.name == "thing")

    assert plugin.component_identity == "plugin/vendor-mkt/thing"


def test_an_orphaned_bundle_is_marked_not_installed(tmp_path):
    """`enabled = false` alone conflates two states: a plugin the user turned
    off is installed and can be turned back on; an orphan is residue that
    cannot. Both are inert, only one is actionable."""
    root = _home(tmp_path)
    _cache_bundle(root, "gone-mkt", "stale")

    graph = build_codex_installed_graph(root)
    plugin = next(r for r in _refs(graph, "plugin") if r.name == "stale")

    assert plugin.extra["installed"] is False
    assert plugin.extra["enabled"] is False


def test_a_config_disabled_plugin_stays_installed(tmp_path):
    """The other half of the distinction: deliberately off, still installed."""
    root = _home(tmp_path)
    _cache_bundle(root, "known-mkt", "widget")
    (root / "config.toml").write_text(
        (root / "config.toml").read_text(encoding="utf-8")
        + '\n[marketplaces.known-mkt]\nsource_type = "local"\nsource = "/tmp/known"\n'
        + '\n[plugins."widget@known-mkt"]\nenabled = false\n',
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)
    plugin = next(r for r in _refs(graph, "plugin") if r.name == "widget")

    assert plugin.extra["enabled"] is False
    assert "installed" not in plugin.extra


def test_components_inside_an_inactive_plugin_inherit_its_state(tmp_path):
    """A skill has no switch of its own — it is loaded because its plugin is.
    Without inheriting, an orphan's skill was indistinguishable from a live
    one except for a blank identity, which reads as a scan failure rather than
    as "the agent never loads this"."""
    root = _home(tmp_path)
    _cache_bundle(root, "gone-mkt", "stale")

    graph = build_codex_installed_graph(root)
    skill = next(r for r in _refs(graph, "skill") if r.name == "packaged")

    assert skill.extra["enabled"] is False
    assert skill.extra["installed"] is False
    assert skill.extra["inactive_via"] == "stale"


def test_components_inside_a_live_plugin_are_not_stamped(tmp_path):
    """Inheritance must not leak: a live plugin's contents carry no inherited
    state at all, so absence keeps meaning "active"."""
    root = _home(tmp_path)
    _cache_bundle(root, "known-mkt", "widget")
    (root / "config.toml").write_text(
        (root / "config.toml").read_text(encoding="utf-8")
        + '\n[marketplaces.known-mkt]\nsource_type = "local"\nsource = "/tmp/known"\n',
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)
    skill = next(r for r in _refs(graph, "skill") if r.name == "packaged")

    assert "inactive_via" not in skill.extra
    assert skill.extra.get("enabled") is None


def test_a_launch_dependency_of_an_inactive_plugins_mcp_server_inherits_state(tmp_path):
    """`_attach_mcp_launch_deps` (inside `finalize_graph`) adds package children
    to an MCP server node after every other seed has run. When that server
    belongs to an inactive plugin, its launch-dependency packages must inherit
    the plugin's state too — inheritance running before launch-dep attachment
    left them looking live."""
    root = _home(tmp_path)
    bundle = _cache_bundle(root, "gone-mkt", "stale")
    (bundle / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"tool_svc": {"command": "npx", "args": ["@scope/orphan-tool@1.2.3"]}}}
        ),
        encoding="utf-8",
    )
    (root / "packages" / "orphan-tool").mkdir(parents=True)
    (root / "packages" / "orphan-tool" / "package.json").write_text(
        json.dumps(
            {
                "name": "@scope/orphan-tool",
                "version": "1.2.3",
                "dependencies": {"left-pad": "1.0.0"},
            }
        ),
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)
    server = next(
        r for r in _refs(graph, "mcp_server") if r.extra["component_path"][-1]["name"] == "tool_svc"
    )
    server_key = next(k for k, n in graph.nodes.items() if n.ref is server)
    package = next(
        graph.nodes[e.child].ref for e in graph.edges if e.parent == server_key
    )

    assert package is not None
    assert package.extra["enabled"] is False
    assert package.extra["installed"] is False
    assert package.extra["inactive_via"] == "stale"


def test_an_orphaned_cache_bundle_is_not_reported_enabled(tmp_path):
    """Neither an enable-map record nor any marketplace declaration.

    On the audited endpoint this was residue from a marketplace Codex no
    longer composes: three bundles Codex cannot load, reported as enabled and
    publishing ~40 components for plugins the agent does not have. Still
    inventoried — an unaudited marketplace source must not silently delete
    real plugins — but not enabled."""
    root = _home(tmp_path)
    _cache_bundle(root, "openai-curated-remote", "stale")

    graph = build_codex_installed_graph(root)
    plugin = next(r for r in _refs(graph, "plugin") if r.name == "stale")

    assert plugin.extra["enabled"] is False
    assert "neither its marketplace nor an enable-map record" in " ".join(graph.warnings)


def test_a_registered_marketplace_without_an_enable_record_still_defaults_enabled(tmp_path):
    """The ambiguous case keeps over-reporting toward active: the bundle comes
    from a registry Codex still composes and only the enable entry is absent."""
    root = _home(tmp_path)
    _cache_bundle(root, "known-mkt", "widget")
    (root / "config.toml").write_text(
        (root / "config.toml").read_text(encoding="utf-8")
        + '\n[marketplaces.known-mkt]\nsource_type = "local"\nsource = "/tmp/known"\n',
        encoding="utf-8",
    )

    graph = build_codex_installed_graph(root)
    plugin = next(r for r in _refs(graph, "plugin") if r.name == "widget")

    assert plugin.extra["enabled"] is True
    assert plugin.component_identity == "plugin/known-mkt/widget"
