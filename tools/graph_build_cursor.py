"""Cursor's declared (repo-scan) composition builder (ADR-0052, ADR-0053).

`build_cursor_graph` is Cursor's `compose` entry point (dispatched by a future
kind registration — Task 8 wires it, and imports this module lazily; this
module never imports `tools.agent_kinds`, preserving ADR-0044's one-way
dependency). It builds the SAME graph shape (`Graph`/`Node`/`Edge`, single
occurrence-keyed node per component, single-parent invariant) that
`tools.graph_build` builds for Claude Code, reusing that module's shared
primitives — never its own privates by underscore import (ADR-0053) — via
the public aliases at the end of `tools/graph_build.py`.

Cursor's declared branch does not reuse `tools.graph_build.descend`'s
TARGET-level walk: that walk assumes a single `config_dir` (`.claude`), and
Cursor reads skills from four roots, has no settings-equivalent surface, and
resolves commands/subagents through Task 4's own precedence resolvers rather
than a directory glob. Per ADR-0053 ("a genuinely different traversal ...
the right response is a second compose path for that kind, not a callback
field"), this module owns that traversal directly. It DOES reuse `descend`
once a plugin root of a `claude_plugin`-shaped format (Cursor's own
`.cursor-plugin`, or the reused `.claude-plugin`) is realized — the bundled
skills/MCP/hooks/commands/agents walk under a realized plugin root is
already fully `RepoSurface`-parameterized and needs no Cursor-specific code.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from tools import cursor_commands, cursor_subagents
from tools.component_ref import ComponentRef
from tools.graph import Graph, Node
from tools.graph_build import (
    add_child,
    add_dep_manifest_packages,
    add_skill_node,
    component_type_of,
    descend,
    descend_into_plugin,
    finalize_graph,
    find_plugin_roots,
    ignore_context,
    is_ignored_under,
    make_normalizer,
    occurrence_key,
    plugin_manifest_path,
    resolve_plugin_format,
    safe_parse,
    same_path,
)
from tools.parsers import agent_plugins, mcp_json
from tools.parsers.claude_plugin_root import resolve_within
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.repo_surface import AGENT_PLUGINS_FORMAT, CURSOR_SURFACE, RepoSurface

# Cache bundles are gated on this zero-byte sentinel, Cursor's own cache-reuse
# check (docs/specs/cursor-agent-kind.md "Exclusions"): a directory without it
# is a cache miss Cursor reinstalls rather than loads, so it is not composition.
_CACHE_COMPLETE_SENTINEL = ".cache-complete"


def build_cursor_graph(
    agent,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Build the composition graph for one Cursor `AgentInstance`.

    Dispatches on `agent.source`: `"declared"` is a repo scan (below);
    `"installed"` scans a real Cursor config root (`_build_cursor_installed`).
    `agent` is duck-typed (never `tools.agent_kinds.AgentInstance` imported
    directly) so this module keeps ADR-0044's one-way dependency:
    `agent_kinds` may import `graph_build_cursor`, never the reverse.
    """
    if agent.source == "installed":
        return _build_cursor_installed(
            agent, include_gitignored=include_gitignored, warnings=warnings
        )
    scan_root = Path(agent.scan_root)
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = make_normalizer("repo", scan_root, scan_root, None, agent.root_label)
    root_spec = None if include_gitignored else load_gitignore_spec(scan_root)

    _descend_cursor_declared(
        graph,
        root,
        scan_root,
        normalize,
        include_gitignored=include_gitignored,
        root_dir=scan_root,
        root_spec=root_spec,
    )

    return finalize_graph(
        graph,
        scan_root,
        normalize,
        include_gitignored=include_gitignored,
        attach_include_gitignored=include_gitignored,
        root_dir=scan_root,
        root_spec=root_spec,
        warnings=warnings,
    )


# Compat roots that are cross-tool conventions, not Cursor-owned state
# (docs/specs/cursor-agent-kind.md "Files Cursor reads that another runtime
# owns"): always resolved against the real home directory, never relocated by
# `--config-dir`. The label is the normalizer's node-key prefix for paths
# under each root — "claude-code" matches the label Claude Code's own kind
# uses for `.claude`, so a `~/.claude/agents/x.md` node keys identically
# regardless of which kind's scan found it.
_HOME_COMPAT_ROOTS: tuple[tuple[str, str], ...] = (
    ("claude-code", ".claude"),
    ("codex", ".codex"),
    ("agents", ".agents"),
)


def _build_cursor_installed(
    agent, *, include_gitignored: bool, warnings: list[str] | None = None
) -> Graph:
    """Build the composition graph for an `agent.source == "installed"`
    Cursor endpoint: `agent.config_root` is Cursor's own config root — never
    relocated, since Cursor declares no root override (ADR-0054), `agent.project_root` an optional
    single workspace folder.

    No gitignore filtering applies (parity with Claude Code's own endpoint
    branch): installed artifacts are not repo source, so every descent below
    passes no `root_dir`/`root_spec` (default `None`, meaning unfiltered).
    """
    config_root = Path(agent.config_root)
    project_root = Path(agent.project_root) if agent.project_root is not None else None
    home = Path.home()
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    extra_roots = tuple((label, home / dirname) for label, dirname in _HOME_COMPAT_ROOTS)
    normalize = make_normalizer(
        "endpoint",
        config_root,
        config_root,
        project_root,
        agent.root_label,
        extra_roots=extra_roots,
    )

    realized_plugin_commands = _realize_installed_plugins(graph, root, config_root, normalize)
    _add_installed_skills(graph, root, config_root, project_root, home, normalize)
    _add_installed_mcps(graph, root, config_root, project_root, normalize)
    _add_installed_commands_and_subagents(
        graph,
        root,
        config_root,
        project_root,
        home,
        normalize,
        realized_plugin_commands=realized_plugin_commands,
    )

    return finalize_graph(
        graph,
        config_root,
        normalize,
        project_root=project_root,
        include_gitignored=include_gitignored,
        attach_include_gitignored=True,
        root_dir=None,
        root_spec=None,
        warnings=warnings,
    )


def _iterdir_dirs(directory: Path) -> list[Path]:
    try:
        return sorted(p for p in directory.iterdir() if p.is_dir())
    except OSError:
        return []


def _realize_installed_plugins(
    graph: Graph, parent: Node, config_root: Path, normalize
) -> list[tuple[Node, Path]]:
    """`plugins/local/<name>/` (dev-linked, symlinks followed — `iterdir`/
    `is_dir` already resolve a symlinked entry) and
    `plugins/cache/<marketplace>/<name>/<sha>/` (gated on
    `_CACHE_COMPLETE_SENTINEL`; an incomplete bundle is skipped entirely, not
    even as a presence-only ref — docs/specs/cursor-agent-kind.md
    "Exclusions"). Reuses the declared branch's single-directory manifest
    resolution (`resolve_plugin_format`) and plugin descent
    (`descend_into_plugin`/`descend`); only the install-root enumeration and
    the cache gate are new here.

    Returns each realized plugin's node paired with its bundled commands
    directory (when it has one) — docs/specs/cursor-agent-kind.md
    "Precedence" places `plugin` as its own tier in Commands' last-wins order
    (team → global → plugin → workspace → personal), so the caller can feed
    these directories into the same precedence resolver used for the
    workspace/personal tiers rather than letting a plugin's bundled command
    permanently shadow — or wrongly survive alongside — the entry that
    should have won.
    """
    plugins_root = config_root / "plugins"
    realized: list[tuple[Node, Path] | None] = []
    for plugin_dir in _iterdir_dirs(plugins_root / "local"):
        realized.append(
            _realize_installed_plugin_dir(graph, parent, plugin_dir, plugin_dir.name, normalize)
        )
    for marketplace_dir in _iterdir_dirs(plugins_root / "cache"):
        for name_dir in _iterdir_dirs(marketplace_dir):
            for sha_dir in _iterdir_dirs(name_dir):
                if not (sha_dir / _CACHE_COMPLETE_SENTINEL).is_file():
                    continue
                realized.append(
                    _realize_installed_plugin_dir(
                        graph,
                        parent,
                        sha_dir,
                        name_dir.name,
                        normalize,
                        marketplace_dir=marketplace_dir.name,
                    )
                )
    return [entry for entry in realized if entry is not None]


def _realize_installed_plugin_dir(
    graph: Graph,
    parent: Node,
    plugin_dir: Path,
    plugin_name: str,
    normalize,
    *,
    marketplace_dir: str | None = None,
) -> tuple[Node, Path] | None:
    fmt = resolve_plugin_format(plugin_dir, CURSOR_SURFACE)
    # `extra["marketplace"]` is the key `canonical_component_identity`
    # (tools/identity.py) treats as verified install state, yielding
    # `plugin/{marketplace}/{name}` — and, because bundled identity is
    # plugin-private, restoring identity to every skill/hook/command/agent
    # inside (ADR-0052). It must be on the ref BEFORE the node is created:
    # `_add_child` finalizes identity on insert, so stamping it afterwards
    # leaves the children already finalized against a namespace-less parent.
    #
    # Set only on this marketplace-cache branch. A dev-linked bundle under
    # `plugins/local/` never carries it, so its self-declared directory name
    # stays occurrence-local — which is the point.
    plugin_extra = (
        {"cursor_marketplace_dir": marketplace_dir, "marketplace": marketplace_dir}
        if marketplace_dir is not None
        else None
    )
    if fmt is AGENT_PLUGINS_FORMAT:
        # §7 excludes commands from the portable Agent Plugins bundle
        # contract, so this format never contributes a commands tier.
        _realize_agent_plugins_root(
            graph,
            parent,
            plugin_dir,
            normalize,
            root_dir=None,
            root_spec=None,
            plugin_extra=plugin_extra,
        )
        return None
    if fmt is not None:
        manifest = plugin_manifest_path(plugin_dir, fmt)
        node = descend_into_plugin(
            graph,
            parent,
            plugin_dir,
            manifest,
            normalize,
            surface=CURSOR_SURFACE,
            plugin_extra=plugin_extra,
        )
        if node is None:
            return None
        commands_dir = _plugin_commands_dir(plugin_dir, fmt)
        return (node, commands_dir) if commands_dir is not None else None
    node = _realize_presence_only_plugin(
        graph, parent, plugin_dir, plugin_name, normalize, plugin_extra=plugin_extra
    )
    commands_dir = _plugin_commands_dir(plugin_dir, fmt)
    return (node, commands_dir) if commands_dir is not None else None


def _plugin_commands_dir(plugin_dir: Path, fmt) -> Path | None:
    """Where a realized plugin's bundled commands live, mirroring the same
    manifest-declared `commands` override `_add_bundled_plugin_surfaces`
    resolves (default `commands/` when absent or when there is no manifest —
    the presence-only branch, `fmt is None`). Returns `None` when the plugin
    has no commands directory on disk, so callers never feed a nonexistent
    tier into precedence resolution.
    """
    declared: str | None = None
    if fmt is not None:
        try:
            data = json.loads(plugin_manifest_path(plugin_dir, fmt).read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict) and isinstance(data.get("commands"), str):
            declared = data["commands"]
    commands_dir = resolve_within(plugin_dir, declared) if declared else (plugin_dir / "commands")
    return commands_dir if commands_dir is not None and commands_dir.is_dir() else None


def _realize_presence_only_plugin(
    graph: Graph,
    parent: Node,
    plugin_dir: Path,
    plugin_name: str,
    normalize,
    *,
    plugin_extra: dict | None = None,
) -> Node:
    """A cached/local bundle whose manifest is absent or unrecognized still
    installs its folder-discovered surfaces (skills/, commands/, agents/,
    hooks/hooks.json, root mcp.json) — `descend`'s `plugin`-kind branch
    already does folder discovery regardless of manifest presence, so only
    the self-ref is synthesized here. No `enabled`/`active` field is ever
    set: enable state is a server call this scan cannot observe.
    """
    self_ref = ComponentRef(
        name=plugin_name,
        component_identity=f"plugin/{plugin_name}",
        source_manifest=str(plugin_dir),
        source_locator="$",
        extra={"component_type": "plugin", "manifest": "absent"},
    )
    if plugin_extra:
        self_ref = replace(self_ref, extra={**(self_ref.extra or {}), **plugin_extra})
    plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
    add_child(graph, parent, plugin_node)
    descend(graph, plugin_node, plugin_dir, normalize, surface=CURSOR_SURFACE)
    return plugin_node


def _add_installed_skills(
    graph: Graph,
    parent: Node,
    config_root: Path,
    project_root: Path | None,
    home: Path,
    normalize,
) -> None:
    """The four user skill roots AND, when a project root is scanned, the same
    four project-scoped roots (docs/specs/cursor-agent-kind.md "Where each
    surface loads from"), each walked recursively; `<config_dir>/skills-cursor`
    is excluded by construction — it is never one of these four roots."""
    roots = []
    if project_root is not None:
        roots += [
            project_root / ".cursor" / "skills",
            project_root / ".agents" / "skills",
            project_root / ".claude" / "skills",
            project_root / ".codex" / "skills",
        ]
    roots += [config_root / "skills"] + [
        home / dirname / "skills" for _, dirname in _HOME_COMPAT_ROOTS
    ]
    for skills_dir in roots:
        if not skills_dir.is_dir():
            continue
        for path in iter_unignored_files(skills_dir, None):
            if path.name != "SKILL.md":
                continue
            add_skill_node(graph, parent, path.parent, normalize=normalize)


def _mcp_server_name(ref: ComponentRef) -> str | None:
    component_path = (ref.extra or {}).get("component_path")
    if isinstance(component_path, list) and component_path:
        last = component_path[-1]
        if isinstance(last, dict) and isinstance(last.get("name"), str):
            return last["name"]
    return None


def _add_installed_mcps(
    graph: Graph, parent: Node, config_root: Path, project_root: Path | None, normalize
) -> None:
    """`<config_root>/mcp.json` (user) and `<project_root>/.cursor/mcp.json`
    (project), merged by server name with project winning
    (docs/specs/cursor-agent-kind.md "Precedence"). One effective server map:
    a name present in both files emits ONLY the project file's ref, so the
    surviving node's `source_manifest` — and therefore posture attribution —
    points at the file it actually won from, never a synthetic merged path.
    A malformed one-sided file (`safe_parse` swallows the failure) never
    drops the other file's entries.
    """
    entries: dict[str, ComponentRef] = {}
    for mcp_path in (
        config_root / "mcp.json",
        *((project_root / ".cursor" / "mcp.json",) if project_root is not None else ()),
    ):
        if not mcp_path.is_file():
            continue
        for ref in safe_parse(graph, mcp_json.parse, mcp_path):
            if component_type_of(ref) != "mcp_server":
                continue
            name = _mcp_server_name(ref)
            if name is not None:
                entries[name] = ref
    for name in sorted(entries):
        ref = entries[name]
        node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
        add_child(graph, parent, node)


def _add_installed_commands_and_subagents(
    graph: Graph,
    parent: Node,
    config_root: Path,
    project_root: Path | None,
    home: Path,
    normalize,
    *,
    realized_plugin_commands: list[tuple[Node, Path]] | None = None,
) -> None:
    """Routes through Task 4's precedence resolvers with explicitly named
    endpoint directories (never a root reconstructed from a directory
    basename): commands last-wins ending in personal `.cursor`, subagents
    first-wins starting with project `.cursor` — each resolver called once,
    over the full project+personal directory order, matching
    `tools/cursor_commands.py`/`tools/cursor_subagents.py`'s own
    `resolve_endpoint` docstring examples.

    `realized_plugin_commands` (from `_realize_installed_plugins`) is the
    `plugin` tier docs/specs/cursor-agent-kind.md "Precedence" places between
    `global` and `workspace` in Commands' last-wins order — each bundled
    command was already emitted as a child of its plugin node by the earlier
    plugin descent, so this only feeds those directories into the SAME
    resolution the workspace/personal tiers go through and then reconciles
    the graph against the outcome: a plugin command that loses to a
    same-relative-path workspace/personal entry is pruned from the plugin
    subtree (Cursor would not load it), and a plugin command that wins is
    left exactly where it already is rather than re-added as a second,
    root-parented node for the same file (which would violate the
    single-parent invariant).
    """
    command_dirs: list[Path] = [
        commands_dir for _, commands_dir in (realized_plugin_commands or [])
    ]
    agent_dirs: list[Path] = []
    if project_root is not None:
        command_dirs += [
            project_root / ".claude" / "commands",
            project_root / ".cursor" / "commands",
        ]
        agent_dirs += [project_root / ".cursor" / "agents", project_root / ".claude" / "agents"]
    command_dirs += [home / ".claude" / "commands", config_root / "commands"]
    agent_dirs += [config_root / "agents", home / ".claude" / "agents"]

    plugin_node_by_commands_dir = {
        commands_dir: node for node, commands_dir in (realized_plugin_commands or [])
    }
    resolved_commands = cursor_commands.resolve_endpoint(command_dirs)
    winner_commands_dir_by_relative_path = {
        resolved.relative_path: resolved.commands_dir for resolved in resolved_commands
    }
    for resolved in resolved_commands:
        if resolved.commands_dir in plugin_node_by_commands_dir:
            # Already realized as a child of its plugin node by the earlier
            # plugin descent — adding it again here would double-parent the
            # same occurrence.
            continue
        _emit_command_agent(
            graph, parent, resolved.file_path, resolved.refs, "command", [], normalize
        )
    for commands_dir, plugin_node in plugin_node_by_commands_dir.items():
        _prune_shadowed_plugin_commands(
            graph, plugin_node, commands_dir, winner_commands_dir_by_relative_path
        )

    for resolved in cursor_subagents.resolve_endpoint(agent_dirs):
        _emit_command_agent(
            graph,
            parent,
            resolved.file_path,
            resolved.refs,
            "agent",
            [],
            normalize,
            parse_error=resolved.parse_error,
        )


def _prune_shadowed_plugin_commands(
    graph: Graph,
    plugin_node: Node,
    commands_dir: Path,
    winner_commands_dir_by_relative_path: dict[str, Path],
) -> None:
    """Detach a plugin's already-realized command child when a
    same-relative-path workspace/personal (or higher-precedence plugin)
    entry won last-wins resolution over it — Cursor loads only the winner,
    so the losing plugin command must not remain in the composed graph.
    """
    try:
        commands_dir_resolved = commands_dir.resolve()
    except (OSError, RuntimeError):
        return
    for child in graph.children_of(plugin_node):
        if child.kind != "command" or child.ref is None or not child.ref.source_manifest:
            continue
        try:
            relative_path = (
                Path(child.ref.source_manifest).resolve().relative_to(commands_dir_resolved)
            ).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        if winner_commands_dir_by_relative_path.get(relative_path) != commands_dir:
            _remove_node(graph, child)


def _remove_node(graph: Graph, node: Node) -> None:
    graph.edges = [e for e in graph.edges if e.parent != node.key and e.child != node.key]
    graph.nodes.pop(node.key, None)


def _is_excluded(path: Path, exclude_resolved: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return True
    return any(resolved.is_relative_to(root) for root in exclude_resolved)


def _prune_shadowed_declared_plugin_commands(
    graph: Graph,
    realized_plugin_commands: list[tuple[Node, Path]],
    resolved_commands: list[cursor_commands.ResolvedCommand],
    exclude_resolved: list[Path],
) -> None:
    """Declared-mode counterpart of `_prune_shadowed_plugin_commands`.

    A realized native plugin's bundled `commands/` tier sits below `workspace`
    in Cursor's last-wins order (docs/specs/cursor-agent-kind.md "Precedence").
    `cursor_commands.resolve_repo` only resolves the workspace tier
    (`.cursor/commands`/`.claude/commands`) — the plugin's own directory is
    never one of its candidates — so a same-relative-path workspace command
    must detach the plugin's already-realized copy rather than let both
    survive. This can NOT reuse `_prune_shadowed_plugin_commands` as-is: that
    function treats "no entry for this relative path" as "shadowed" (safe
    there because `resolve_endpoint` always includes the plugin's own
    directory as a candidate, so an unshadowed plugin command is its own
    entry's winner); here an unshadowed plugin command has no entry at all,
    so the same test would prune every plugin command unconditionally.

    Scoped to the workspace group the plugin is nested under — a resolved
    command's group root is its `commands_dir`'s grandparent, per
    `resolve_repo`'s own `commands_dir.parent.parent` grouping — so an
    unrelated workspace elsewhere in a multi-root repo scan sharing the same
    relative filename by coincidence never shadows a plugin outside its tree.

    `exclude_resolved` (the same realized-plugin-root list `_emit_command_agent`
    filters against) is applied here too: a `resolve_repo` hit that sits
    inside an already-realized plugin subtree — e.g. a bundled fixture like
    `vendor/.cursor/commands/deploy.md` nested under `vendor`'s own realized
    root — is content Cursor never independently loads and was never emitted
    as a node. Counting it toward `overridden_relative_paths` anyway would
    prune the plugin's real bundled command in its favor and leave neither
    survive in the graph.
    """
    independent_commands = [
        resolved
        for resolved in resolved_commands
        if not _is_excluded(resolved.file_path, exclude_resolved)
    ]
    for plugin_node, commands_dir in realized_plugin_commands:
        try:
            commands_dir_resolved = commands_dir.resolve()
        except (OSError, RuntimeError):
            continue
        overridden_relative_paths: set[str] = set()
        for resolved in independent_commands:
            try:
                group_root = resolved.commands_dir.resolve().parent.parent
            except (OSError, RuntimeError):
                continue
            if commands_dir_resolved.is_relative_to(group_root):
                overridden_relative_paths.add(resolved.relative_path)
        if not overridden_relative_paths:
            continue
        for child in graph.children_of(plugin_node):
            if child.kind != "command" or child.ref is None or not child.ref.source_manifest:
                continue
            try:
                relative_path = (
                    Path(child.ref.source_manifest).resolve().relative_to(commands_dir_resolved)
                ).as_posix()
            except (OSError, RuntimeError, ValueError):
                continue
            if relative_path in overridden_relative_paths:
                _remove_node(graph, child)


def _descend_cursor_declared(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize,
    *,
    include_gitignored: bool,
    root_dir: Path,
    root_spec,
) -> None:
    realized_roots, realized_plugin_commands = _realize_plugins(
        graph,
        parent,
        directory,
        normalize,
        include_gitignored=include_gitignored,
        root_dir=root_dir,
        root_spec=root_spec,
    )
    _add_cursor_skills(
        graph,
        parent,
        directory,
        normalize,
        exclude_under=realized_roots,
        include_gitignored=include_gitignored,
        root_dir=root_dir,
        root_spec=root_spec,
    )
    _add_scoped_mcps(
        graph,
        parent,
        directory,
        normalize,
        exclude_under=realized_roots,
        include_gitignored=include_gitignored,
        root_dir=root_dir,
        root_spec=root_spec,
    )
    _add_commands_and_subagents(
        graph,
        parent,
        directory,
        normalize,
        exclude_under=realized_roots,
        include_gitignored=include_gitignored,
        root_dir=root_dir,
        root_spec=root_spec,
        realized_plugin_commands=realized_plugin_commands,
    )
    # The scan root's own bare dep manifests (parity with Claude Code's
    # target-level `_add_dep_manifest_packages` call): skipped when the scan
    # root itself is a realized plugin root, since that plugin already owns
    # its own root dep manifests via the plugin-branch descent.
    if not any(same_path(directory, root) for root in realized_roots):
        add_dep_manifest_packages(
            graph,
            parent,
            directory,
            normalize,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
        )


def realized_plugin_roots(
    directory: Path, surface: RepoSurface, *, include_gitignored: bool = False
) -> list[Path]:
    """Every directory under `directory` where one of `surface`'s plugin
    formats actually realizes, without building a `Graph`.

    Kind-neutral despite this module's name: the format list, the manifest
    location, and the parser all come from `surface`, so a third kind gets
    this by declaring its formats rather than by copying this function.

    This is the read-only counterpart of `_realize_plugins`'s realized-root
    computation: same candidate discovery, same ancestors-before-descendants
    ordering, and the same qualified/realized distinction (a manifest that
    qualifies for discovery but yields no self-ref owns no subtree). It
    exists for callers that need to know which subtrees composition already
    claims WITHOUT paying for a full graph build — Cursor evidence detection
    (a nested plugin fixture inside an already-realized root must not trip a
    phantom Cursor BOM) and repo-mode parse-count accounting (that same
    nested fixture must not inflate `source_unit_count` or register a
    `parse_failed` for content Cursor never loads). Both would otherwise have
    to restate `_realize_plugins`'s exclusion rules by hand, and each rule
    restated by hand is a rule that can drift from the one composition
    actually applies.
    """
    candidates = find_plugin_roots(directory, surface, include_gitignored=include_gitignored)
    ordered = sorted(candidates, key=lambda entry: len(entry[0].resolve().parts))
    realized_roots: list[Path] = []
    for candidate_root, fmt in ordered:
        resolved = candidate_root.resolve()
        if any(resolved != other and resolved.is_relative_to(other) for other in realized_roots):
            continue
        if fmt.parse is None:
            continue
        # `manifest_dir=""` (Agent Plugins, manifest at the root itself) joins
        # away cleanly, so the two format shapes need no branch here.
        manifest = candidate_root / fmt.manifest_dir / fmt.manifest_filename
        try:
            refs = fmt.parse(manifest)
        except Exception:
            continue
        if any((ref.extra or {}).get("component_type") == "plugin" for ref in refs):
            realized_roots.append(resolved)
    return realized_roots


def plugin_manifest_root(path: Path, surface: RepoSurface = CURSOR_SURFACE) -> Path | None:
    """The plugin root directory `path` (a plugin manifest file) belongs to,
    per `surface`'s format conventions — mirrors `_find_plugin_roots`'s
    own per-candidate root derivation, so a manifest's OWN root is computed
    the same way here as it is during discovery. This matters because a
    manifest-dir format (`.cursor-plugin`/`.claude-plugin`) puts the manifest
    ONE directory below its own root, while the flat Agent Plugins format
    does not — using `path.parent` unconditionally as "the root" would treat
    a manifest-dir-format plugin's OWN manifest as nested one level below
    itself.

    Returns `None` when `path` doesn't match any recognized format's
    manifest filename/directory shape at all. The surface must be the one
    whose ownership is being tested: a `.codex-plugin` manifest is invisible
    to Cursor's format list, and treating an unrecognized manifest as "not a
    root" would make a plugin's OWN manifest look like bundle content nested
    beneath itself.
    """
    for fmt in surface.plugin_formats:
        if path.name != fmt.manifest_filename:
            continue
        if fmt.manifest_dir:
            if path.parent.name != fmt.manifest_dir:
                continue
            return path.parent.parent
        return path.parent
    return None


def is_owned_by_realized_plugin(
    path: Path, realized_roots: list[Path], surface: RepoSurface = CURSOR_SURFACE
) -> bool:
    """True when `path` is content of an already-realized plugin rather than
    an independent declaration.

    Ownership is plain ancestry over `path` itself, NOT over the plugin root
    `path` might define. An earlier form asked only "is this *manifest*
    nested?", which silently answered `False` for every non-manifest surface
    — a bundled `examples/.cursor/mcp.json`, `.cursor/commands/demo.md`, or
    `.cursor/skills/demo/SKILL.md` all sailed through and declared a phantom
    agent. Composition already excludes that content wholesale, so anything
    beneath a realized root is owned, whatever its shape.

    The one exception is a realized root's OWN defining manifest: it sits
    beneath itself but IS the independent declaration, so it stays visible.
    """
    resolved = path.resolve()
    own_root = plugin_manifest_root(path, surface)
    own = own_root.resolve() if own_root is not None else None
    return any(resolved.is_relative_to(root) and own != root for root in realized_roots)


def _realize_plugins(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize,
    *,
    include_gitignored: bool,
    root_dir: Path,
    root_spec,
) -> tuple[list[Path], list[tuple[Node, Path]]]:
    """Realize every qualifying plugin root under `directory`, once each.

    `find_plugin_roots` already collapses same-directory candidates to one
    entry (ADR-0053's first-qualifying-candidate precedence via
    `_resolve_plugin_format`), which is what keeps a directory carrying BOTH
    `.cursor-plugin/plugin.json` and a root `plugin.json` from realizing
    twice (the single-parent hazard this task's brief calls out).

    The remaining hazard is a NESTED one: any plugin bundle's own fixture
    content (e.g. `examples/demo/plugin.json`, a DIFFERENT directory than its
    containing root) must not realize as an independent bundle when it sits
    strictly below an already-realized root — of ANY format, not just a
    native one. An Agent Plugins bundle can carry a nested Agent Plugins
    fixture just as easily as a native bundle can carry a nested native one,
    and either shape must be excluded the same way a nested Agent Plugins
    fixture under a native root already is.
    #
    # Candidates are processed ancestors-before-descendants (shallowest path
    # first) so a root's realization result is known before any candidate
    # nested under it is considered — the exclusion set can only be built
    # from roots that actually REALIZED, not from every candidate. A
    # `.cursor-plugin/plugin.json` with an empty `name` qualifies for
    # discovery but yields no self-ref, so `descend_into_plugin` returns
    # `None` and it owns nothing — excluding a valid root beneath it would
    # drop a real bundle on behalf of a plugin that does not exist in the
    # graph. "Qualified" and "realized" are different sets and only the
    # second confers ownership.

    Also returns each realized native plugin's node paired with its bundled
    commands directory (when it has one), mirroring
    `_realize_installed_plugins`'s same return shape: Commands' last-wins
    order places `plugin` between `global` and `workspace`
    (docs/specs/cursor-agent-kind.md "Precedence"), so `_add_commands_and_subagents`
    can reconcile a plugin's bundled command against a same-relative-path
    workspace command instead of letting both survive in the graph.
    """
    candidates = find_plugin_roots(directory, CURSOR_SURFACE, include_gitignored=include_gitignored)
    ordered = sorted(candidates, key=lambda entry: len(entry[0].resolve().parts))
    realized: list[Path] = []
    realized_roots: list[Path] = []
    realized_plugin_commands: list[tuple[Node, Path]] = []

    def _strictly_below_realized(root: Path) -> bool:
        resolved = root.resolve()
        return any(resolved != other and resolved.is_relative_to(other) for other in realized_roots)

    for root, fmt in ordered:
        if _strictly_below_realized(root):
            continue
        if fmt is AGENT_PLUGINS_FORMAT:
            node = _realize_agent_plugins_root(
                graph,
                parent,
                root,
                normalize,
                root_dir=root_dir,
                root_spec=root_spec,
            )
        else:
            manifest = root / fmt.manifest_dir / fmt.manifest_filename
            node = descend_into_plugin(
                graph,
                parent,
                root,
                manifest,
                normalize,
                root_dir=root_dir,
                root_spec=root_spec,
                surface=CURSOR_SURFACE,
            )
        if node is not None:
            realized.append(root)
            realized_roots.append(root.resolve())
            if fmt is not AGENT_PLUGINS_FORMAT:
                commands_dir = _plugin_commands_dir(root, fmt)
                if commands_dir is not None:
                    realized_plugin_commands.append((node, commands_dir))
    return realized, realized_plugin_commands


def _realize_agent_plugins_root(
    graph: Graph,
    parent: Node,
    plugin_root: Path,
    normalize,
    *,
    root_dir: Path | None,
    root_spec,
    plugin_extra: dict | None = None,
) -> Node | None:
    """Realize an Agent Plugins bundle: `agent_plugins.parse` returns the
    plugin self-ref, its (one-level) skill refs, and its MCP server refs as
    one flat list — placement (parent-by-construction) is owned here, same
    division of labor as `claude_plugin.parse` + `descend_into_plugin`.

    Returns `None` (realizing nothing) when the manifest fails validation —
    the caller must not exclude `plugin_root` from sibling discovery in that
    case, matching `descend_into_plugin`'s own contract. `strict=True`
    (`_resolve_plugin_format` already confirmed this file's `$schema`
    qualifies, so a failure here is a real defect, not a guard miss) makes
    `safe_parse` record the failure as a warning instead of the scan
    silently reporting a clean, empty composition for it.
    """
    manifest = plugin_root / "plugin.json"
    refs = safe_parse(graph, lambda path: agent_plugins.parse(path, strict=True), manifest)
    original_self_ref = next((r for r in refs if component_type_of(r) == "plugin"), None)
    if original_self_ref is None:
        return None
    self_ref = original_self_ref
    eval_root, spec = ignore_context(plugin_root, False, root_dir, root_spec)
    if self_ref.source_manifest and is_ignored_under(
        Path(self_ref.source_manifest), eval_root, spec
    ):
        return None
    if plugin_extra:
        # `replace` returns a NEW object, so the skip check below must compare
        # against `original_self_ref` (still the object sitting in `refs`) —
        # comparing against the reassigned `self_ref` would let the pre-extra
        # ref fall through the loop and re-emit as a second, marketplace-less
        # nested plugin.
        self_ref = replace(self_ref, extra={**(self_ref.extra or {}), **plugin_extra})
    plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
    add_child(graph, parent, plugin_node)
    for ref in refs:
        if ref is original_self_ref:
            continue
        kind = component_type_of(ref)
        if not isinstance(kind, str):
            continue
        if ref.source_manifest and is_ignored_under(Path(ref.source_manifest), eval_root, spec):
            continue
        child_node = Node(key=occurrence_key(ref, normalize), kind=kind, ref=ref)
        add_child(graph, plugin_node, child_node)
        if kind == "skill":
            # §7.1 skill discovery is already one-level (agent_plugins.parse
            # enforces it); the skill's OWN dep manifests still need the
            # ordinary skill-branch descent (agent-dependency packages).
            skill_dir = Path(ref.source_manifest).parent if ref.source_manifest else None
            if skill_dir is not None:
                _add_skill_descend(graph, child_node, skill_dir, normalize, root_dir, root_spec)
    return plugin_node


def _add_skill_descend(graph, skill_node, skill_dir, normalize, root_dir, root_spec) -> None:
    descend(graph, skill_node, skill_dir, normalize, root_dir=root_dir, root_spec=root_spec)


def _add_cursor_skills(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize,
    *,
    exclude_under: list[Path],
    include_gitignored: bool,
    root_dir: Path,
    root_spec,
) -> None:
    """`.cursor/skills/`, `.agents/skills/`, `.claude/skills/`, `.codex/skills/`
    at any depth in the tree, each walked *recursively* beneath its `skills`
    dir (unlike Claude Code's own one-level project-skill walk, and unlike
    Agent Plugins' one-level bundled skills) — per
    docs/specs/cursor-agent-kind.md's Skills row ("Traversal: recursive").
    `excluded_skill_dirs` (`skills-cursor`, Cursor's vendor built-ins) is a
    SIBLING of `skills/`, never nested inside it, so it is excluded by the
    exact `"skills"` segment match below without a separate check — the
    field is still consulted explicitly so a future loosening of that match
    can't silently start inventorying it.
    """
    eval_root, spec = ignore_context(directory, include_gitignored, root_dir, root_spec)
    walk_spec = spec if eval_root == directory else None
    exclude_resolved = [p.resolve() for p in exclude_under]
    for path in iter_unignored_files(directory, walk_spec):
        if path.name != "SKILL.md":
            continue
        if is_ignored_under(path, eval_root, spec):
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
            continue
        if not _is_cursor_skill_md(path, directory):
            continue
        add_skill_node(
            graph,
            parent,
            path.parent,
            normalize=normalize,
            root_dir=root_dir,
            root_spec=root_spec,
        )


def _is_cursor_skill_md(path: Path, root: Path, surface: RepoSurface = CURSOR_SURFACE) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    if not parts or parts[-1] != "SKILL.md":
        return False
    for i in range(len(parts) - 2):
        if parts[i] not in surface.skill_config_dirs:
            continue
        if parts[i + 1] in surface.excluded_skill_dirs:
            continue
        if parts[i + 1] != surface.project_skills_subdir:
            continue
        return True
    return False


def _add_scoped_mcps(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize,
    *,
    exclude_under: list[Path],
    include_gitignored: bool,
    root_dir: Path,
    root_spec,
    surface: RepoSurface = CURSOR_SURFACE,
) -> None:
    """`.cursor/mcp.json` at any depth (one workspace folder's declaration
    each) — a SCOPED relative-path match, unlike Claude Code's any-name
    `standalone_mcp_filenames` pattern, because Cursor's direct MCP surface
    is exactly this one path (docs/specs/cursor-agent-kind.md "Where each
    surface loads from").
    """
    eval_root, spec = ignore_context(directory, include_gitignored, root_dir, root_spec)
    walk_spec = spec if eval_root == directory else None
    exclude_resolved = [p.resolve() for p in exclude_under]
    for path in iter_unignored_files(directory, walk_spec):
        try:
            rel = path.relative_to(directory).as_posix()
        except ValueError:
            continue
        # Any-depth match on the scoped rel (one declaration per workspace
        # folder, not just the scan root): `.cursor/mcp.json` itself, or
        # `.../.cursor/mcp.json` nested under a subdirectory.
        if not any(
            rel == scoped_rel or rel.endswith(f"/{scoped_rel}")
            for scoped_rel in surface.scoped_mcp_rels
        ):
            continue
        if is_ignored_under(path, eval_root, spec):
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
            continue
        for ref in safe_parse(graph, mcp_json.parse, path):
            if component_type_of(ref) != "mcp_server":
                continue
            node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
            add_child(graph, parent, node)


def _add_commands_and_subagents(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize,
    *,
    exclude_under: list[Path],
    include_gitignored: bool,
    root_dir: Path,
    root_spec,
    realized_plugin_commands: list[tuple[Node, Path]] | None = None,
) -> None:
    """Route through Task 4's precedence resolvers rather than walking
    `.cursor/commands`/`.claude/commands`/`.cursor/agents`/`.claude/agents`
    directly: `tools.cursor_commands`/`tools.cursor_subagents` already
    implement the (opposite-direction) precedence rules and containment
    checks those surfaces need.

    Neither resolver is gitignore-aware itself (both walk with unrestricted
    `Path.rglob`), so each resolver is given an `is_ignored` predicate built
    from the same `ignore_context`/`is_ignored_under` gate `_add_cursor_skills`/
    `_add_scoped_mcps` use — evaluated PER CANDIDATE FILE, before precedence
    resolution picks a winner for a given relative path, not only on the
    winner afterwards. Filtering only the winner would let a gitignored
    higher-precedence file (e.g. an ignored `.cursor/commands/x.md`) win
    resolution and then get dropped, silently shadowing an unignored
    lower-precedence file (`.claude/commands/x.md`) that should have surfaced
    instead.

    `realized_plugin_commands` (from `_realize_plugins`) is the `plugin` tier
    docs/specs/cursor-agent-kind.md "Precedence" places between `global` and
    `workspace` in Commands' last-wins order. `cursor_commands.resolve_repo`
    only resolves the workspace tier (it has no notion of a bundled plugin
    directory), so a realized plugin's bundled command is reconciled
    separately, after the workspace tier resolves, rather than folded into
    the same precedence walk.
    """
    exclude_resolved = [p.resolve() for p in exclude_under]
    eval_root, spec = ignore_context(directory, include_gitignored, root_dir, root_spec)

    def _is_ignored(path: Path) -> bool:
        try:
            resolved_path = path.resolve()
        except (OSError, RuntimeError):
            return True
        return is_ignored_under(resolved_path, eval_root, spec)

    resolved_commands = cursor_commands.resolve_repo(directory, is_ignored=_is_ignored)
    for resolved in resolved_commands:
        _emit_command_agent(
            graph,
            parent,
            resolved.file_path,
            resolved.refs,
            "command",
            exclude_resolved,
            normalize,
            eval_root=eval_root,
            spec=spec,
        )
    _prune_shadowed_declared_plugin_commands(
        graph, realized_plugin_commands or [], resolved_commands, exclude_resolved
    )
    for resolved in cursor_subagents.resolve_repo(directory, is_ignored=_is_ignored):
        _emit_command_agent(
            graph,
            parent,
            resolved.file_path,
            resolved.refs,
            "agent",
            exclude_resolved,
            normalize,
            eval_root=eval_root,
            spec=spec,
            parse_error=resolved.parse_error,
        )


def _emit_command_agent(
    graph: Graph,
    parent: Node,
    file_path: Path,
    refs: tuple,
    kind: str,
    exclude_resolved: list[Path],
    normalize,
    *,
    eval_root: Path | None = None,
    spec=None,
    parse_error: str | None = None,
) -> None:
    """One command/subagent file → one self node (child of `parent`) plus,
    for subagents, any frontmatter `mcpServers`/`hooks` children `parse_file`
    returned after the self ref (parity with the `.md` branch of
    `tools.graph_build._add_repo_standalone_components`).

    `eval_root`/`spec` are `None` for the installed-mode caller (no gitignore
    filtering applies there); the declared-mode caller always passes both.

    `parse_error`, when set, is `cursor_subagents.ResolvedSubagent.parse_error`
    — a strict-parse failure with no refs to show for it. Recorded as a graph
    warning so a malformed subagent is reported rather than silently
    composing nothing — but only once the exclusion/ignore checks below have
    confirmed this file is an independent declaration Cursor would actually
    load; a malformed fixture owned by an already-realized plugin subtree
    (or gitignored) is content Cursor never loads, so it must not surface a
    warning either.
    """
    try:
        resolved = file_path.resolve()
    except (OSError, RuntimeError):
        return
    if any(resolved.is_relative_to(root) for root in exclude_resolved):
        return
    if eval_root is not None and is_ignored_under(resolved, eval_root, spec):
        return
    if parse_error is not None:
        graph.warnings.append(parse_error)
    if not refs:
        return
    self_node = Node(key=occurrence_key(refs[0], normalize), kind=kind, ref=refs[0])
    add_child(graph, parent, self_node)
    for child_ref in refs[1:]:
        child_kind = component_type_of(child_ref)
        if not isinstance(child_kind, str):
            continue
        child_node = Node(key=occurrence_key(child_ref, normalize), kind=child_kind, ref=child_ref)
        add_child(graph, self_node, child_node)
