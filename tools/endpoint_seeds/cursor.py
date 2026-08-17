"""Cursor's endpoint-mode composition — the seed_endpoint value for
HOSTS["cursor"]. Unlike Claude Code's, this has no lockfile-backed
install-state to resolve: MCP servers, Skills, and Commands are direct
file reads; Plugins are scoped to presence only (dev-linked and
marketplace-cached), with no enabled-state property, per ADR-0045
Decision #7 and ADR-0045 Decision #7. Subagents are seeded by build_graph's cross-host
pass, never here.
"""

from __future__ import annotations

import functools
from dataclasses import replace
from pathlib import Path
from typing import Optional

from tools.component_ref import ComponentRef
from tools.endpoint_request import shared_agents_root
from tools.graph import Graph, Node
from tools.graph_build import (
    SourceNormalizer,
    _add_child,
    _add_skills_from_dir,
    _component_type,
    _realize_agent_plugin,
    _safe_parse,
    descend,
    occurrence_key,
)
from tools.parsers import claude_command_agent, claude_plugin, mcp_json
from tools.parsers.agent_plugins import is_agent_plugins_manifest
from tools.parsers.gitignore import load_gitignore_spec


def seed_endpoint(
    graph: Graph,
    target: Node,
    config_root: Path,
    project_root: Optional[Path],
    normalize: SourceNormalizer,
    *,
    warnings: Optional[list[str]] = None,
) -> None:
    _seed_remote_mcps(graph, target, config_root, project_root, normalize)
    _seed_direct_skills(graph, target, config_root, project_root, normalize)
    _seed_commands(graph, target, project_root, normalize)
    _seed_dev_linked_plugins(graph, target, config_root, normalize, warnings=warnings)
    _seed_marketplace_cached_plugins(graph, target, config_root, normalize, warnings=warnings)


_MCP_PARSE = functools.partial(mcp_json.parse, runtime_hosts=["cursor"])


def _seed_remote_mcps(graph, target, config_root, project_root, normalize) -> None:
    mcp_paths = [config_root / "mcp.json"]
    if project_root is not None:
        mcp_paths.append(project_root / ".cursor" / "mcp.json")
    for mcp_path in mcp_paths:
        if not mcp_path.is_file():
            continue
        for ref in _safe_parse(_MCP_PARSE, mcp_path):
            if _component_type(ref) != "mcp_server":
                continue
            node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
            _add_child(graph, target, node)


def _config_skill_roots(config_root: Path) -> list[Path]:
    # The two config-scoped Cursor skill roots (spec's Skills section). The
    # shared `~/.agents/skills` root comes from `shared_agents_root()`
    # (tools.endpoint_request) rather than being derived from config_root: it
    # is a cross-tool, home-scoped convention, not Cursor-owned state, so a
    # `--config-dir` override must not relocate it. Neither lives inside a
    # project's `.gitignore` scope, so no root_dir/root_spec is threaded.
    return [config_root / "skills", shared_agents_root() / "skills"]


def _project_skill_roots(project_root: Path) -> list[Path]:
    # The two project-scoped Cursor skill roots (spec's Skills section).
    return [project_root / ".cursor" / "skills", project_root / ".agents" / "skills"]


def _seed_direct_skills(graph, target, config_root, project_root, normalize) -> None:
    for skills_root in _config_skill_roots(config_root):
        _add_skills_from_dir(
            graph, target, skills_root, normalize=normalize, runtime_hosts=["cursor"]
        )
    if project_root is None:
        return
    # Parity with Claude's endpoint project skills (endpoint_seeds/claude_code.py's
    # _add_project_skills call): thread the project root's .gitignore so a
    # skill under an ignored path (e.g. `.worktrees/`) isn't inventoried.
    project_skill_spec = load_gitignore_spec(project_root)
    for skills_root in _project_skill_roots(project_root):
        _add_skills_from_dir(
            graph,
            target,
            skills_root,
            normalize=normalize,
            runtime_hosts=["cursor"],
            root_dir=project_root,
            root_spec=project_skill_spec,
        )


def _seed_commands(graph, target, project_root, normalize) -> None:
    if project_root is None:
        return
    commands_dir = project_root / ".cursor" / "commands"
    for ref in claude_command_agent.enumerate_dir(
        commands_dir, kind="command", scope_owner=None, runtime_hosts=["cursor"]
    ):
        node = Node(key=occurrence_key(ref, normalize), kind="command", ref=ref)
        _add_child(graph, target, node)


_NATIVE_PARSE = functools.partial(claude_plugin.parse, runtime_hosts=["cursor"])

# Location-derived, not enable-state: every endpoint-discovered Cursor plugin
# lives under `~/.cursor`, the user config root, and the spec's Plugins
# section documents no project-level Cursor plugin install path — so
# `plugin_scope` is "user" for both dev-linked and marketplace-cached
# plugins, parity with Claude's lockfile-recorded scope.
_USER_SCOPE_EXTRA: dict[str, object] = {"scope": "user"}


def _seed_dev_linked_plugins(graph, target, config_root, normalize, *, warnings) -> None:
    plugins_local = config_root / "plugins" / "local"
    if not plugins_local.is_dir():
        return
    for plugin_dir in sorted(plugins_local.iterdir()):
        _realize_plugin_bundle(
            graph, target, plugin_dir, normalize, self_ref_extra=_USER_SCOPE_EXTRA
        )


def _realize_plugin_bundle(
    graph, target, plugin_dir: Path, normalize, *, self_ref_extra: dict[str, object] | None = None
) -> bool:
    """Realize one plugin bundle directory (dev-linked or marketplace-cached)
    against `target`, native-format-wins-over-Agent-Plugins, and report
    whether anything was realized.

    Both formats, per the spec's Plugins endpoint-mode note: the native
    `.cursor-plugin/plugin.json` AND an Agent Plugins root `plugin.json`.
    When one directory carries both, the NATIVE format wins and the root
    `plugin.json` is not realized — the same rule repo mode already
    enforces, where a realized native root's subtree exclusion covers the
    bundle-root plugin.json. Realizing both would walk the same `skills/`
    and the same default root `mcp.json` twice, putting one occurrence
    under two plugin parents and aborting the whole scan. A native manifest
    that fails to realize (corrupt JSON, no self ref) claims nothing, so
    the Agent Plugins manifest still gets its turn.

    `self_ref_extra`, when given, is merged onto the plugin self ref's
    `extra` only (ADR-0045 Decision #7's cached-plugin caller stamps
    `cursor_marketplace_dir` this way; dev-linked callers omit it, so a
    dev-linked ref never carries the key).
    """
    native = plugin_dir / ".cursor-plugin" / "plugin.json"
    if native.is_file():
        # claude_plugin.parse raises on bad JSON (unlike the [] -on-failure
        # agent_plugins/is_agent_plugins_manifest pair below), so go through
        # _safe_parse: one corrupt manifest must cost exactly this plugin,
        # not the wider scan.
        refs = _safe_parse(_NATIVE_PARSE, native)
        self_ref = next((r for r in refs if _component_type(r) == "plugin"), None)
        if self_ref is not None:
            if self_ref_extra:
                self_ref = replace(self_ref, extra={**self_ref.extra, **self_ref_extra})
            plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
            _add_child(graph, target, plugin_node)
            # emit_own_root_deps stays at its default (True): Cursor has
            # no tier-2 lockfile walk to supply the plugin's own root
            # dep manifests instead, unlike Claude's endpoint plugin path.
            descend(graph, plugin_node, plugin_dir, normalize)
            return True
    root_manifest = plugin_dir / "plugin.json"
    if root_manifest.is_file() and is_agent_plugins_manifest(root_manifest):
        # The closed, skills+MCP-only realization (Task 14) — never the
        # native descent above, which would enumerate the client-private
        # surfaces the portable Agent Plugins contract excludes.
        node = _realize_agent_plugin(
            graph, target, root_manifest, normalize, self_ref_extra=self_ref_extra
        )
        return node is not None
    return False


def _seed_marketplace_cached_plugins(graph, target, config_root, normalize, *, warnings) -> None:
    cache_root = config_root / "plugins" / "cache"
    if not cache_root.is_dir():
        return
    for marketplace_dir in sorted(p for p in cache_root.iterdir() if p.is_dir()):
        for plugin_dir in sorted(p for p in marketplace_dir.iterdir() if p.is_dir()):
            for version_dir in sorted(p for p in plugin_dir.iterdir() if p.is_dir()):
                if not (version_dir / ".cache-complete").is_file():
                    continue
                # ADR-0045 Decision #7 point 3: the marketplace directory name is an
                # observed path segment, not verified install-state, so it
                # is recorded as non-identity provenance under
                # `cursor_marketplace_dir` — never `extra["marketplace"]`,
                # which mints qualified cross-BOM identity.
                extra = {**_USER_SCOPE_EXTRA, "cursor_marketplace_dir": marketplace_dir.name}
                realized = _realize_plugin_bundle(
                    graph, target, version_dir, normalize, self_ref_extra=extra
                )
                if not realized:
                    _seed_manifest_less_cached_plugin(
                        graph, target, version_dir, plugin_dir.name, normalize, extra
                    )


def _seed_manifest_less_cached_plugin(
    graph, target, version_dir: Path, name: str, normalize, extra: dict[str, object]
) -> None:
    # ADR-0045 Decision #7 point 4: a real cached bundle (the "granola" marketplace
    # plugin) ships neither manifest format, yet Cursor loads it. Skipping it
    # silently would reproduce the exact invisibility this ADR exists to fix,
    # so synthesize a presence-only self ref from the directory segment and
    # walk its bundled skills/commands directly — the shared native descent
    # can't be reused here because its bundled-MCP-default-filename
    # detection (`default_mcp_filename_for_manifest`) keys off a REAL
    # `.cursor-plugin`/`.claude-plugin` manifest path, which this bundle does
    # not have, and fabricating one would misdetect the default MCP filename.
    self_ref = ComponentRef(
        name=name,
        component_identity=f"plugin/{name}",
        source_manifest=str(version_dir / ".cache-complete"),
        source_locator="$",
        extra={
            "component_type": "plugin",
            "runtime_hosts": ["cursor"],
            "manifest": "absent",
            **extra,
        },
    )
    plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
    _add_child(graph, target, plugin_node)
    _add_skills_from_dir(
        graph,
        plugin_node,
        version_dir / "skills",
        normalize=normalize,
        plugin_root=version_dir,
        runtime_hosts=["cursor"],
    )
    for ref in claude_command_agent.enumerate_dir(
        version_dir / "commands", kind="command", scope_owner=name, runtime_hosts=["cursor"]
    ):
        node = Node(key=occurrence_key(ref, normalize), kind="command", ref=ref)
        _add_child(graph, plugin_node, node)
