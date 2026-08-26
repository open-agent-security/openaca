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

    _realize_installed_plugins(graph, root, config_root, normalize)
    _add_installed_skills(graph, root, config_root, home, normalize)
    _add_installed_mcps(graph, root, config_root, project_root, normalize)
    _add_installed_commands_and_subagents(graph, root, config_root, project_root, home, normalize)

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


def _realize_installed_plugins(graph: Graph, parent: Node, config_root: Path, normalize) -> None:
    """`plugins/local/<name>/` (dev-linked, symlinks followed — `iterdir`/
    `is_dir` already resolve a symlinked entry) and
    `plugins/cache/<marketplace>/<name>/<sha>/` (gated on
    `_CACHE_COMPLETE_SENTINEL`; an incomplete bundle is skipped entirely, not
    even as a presence-only ref — docs/specs/cursor-agent-kind.md
    "Exclusions"). Reuses the declared branch's single-directory manifest
    resolution (`resolve_plugin_format`) and plugin descent
    (`descend_into_plugin`/`descend`); only the install-root enumeration and
    the cache gate are new here.
    """
    plugins_root = config_root / "plugins"
    for plugin_dir in _iterdir_dirs(plugins_root / "local"):
        _realize_installed_plugin_dir(graph, parent, plugin_dir, plugin_dir.name, normalize)
    for marketplace_dir in _iterdir_dirs(plugins_root / "cache"):
        for name_dir in _iterdir_dirs(marketplace_dir):
            for sha_dir in _iterdir_dirs(name_dir):
                if not (sha_dir / _CACHE_COMPLETE_SENTINEL).is_file():
                    continue
                _realize_installed_plugin_dir(
                    graph,
                    parent,
                    sha_dir,
                    name_dir.name,
                    normalize,
                    marketplace_dir=marketplace_dir.name,
                )


def _realize_installed_plugin_dir(
    graph: Graph,
    parent: Node,
    plugin_dir: Path,
    plugin_name: str,
    normalize,
    *,
    marketplace_dir: str | None = None,
) -> None:
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
        _realize_agent_plugins_root(
            graph,
            parent,
            plugin_dir,
            normalize,
            root_dir=None,
            root_spec=None,
            plugin_extra=plugin_extra,
        )
    elif fmt is not None:
        manifest = plugin_manifest_path(plugin_dir, fmt)
        descend_into_plugin(
            graph,
            parent,
            plugin_dir,
            manifest,
            normalize,
            surface=CURSOR_SURFACE,
            plugin_extra=plugin_extra,
        )
    else:
        _realize_presence_only_plugin(
            graph, parent, plugin_dir, plugin_name, normalize, plugin_extra=plugin_extra
        )


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
    graph: Graph, parent: Node, config_root: Path, home: Path, normalize
) -> None:
    """The four user skill roots (docs/specs/cursor-agent-kind.md "Where each
    surface loads from"), each walked recursively; `<config_dir>/skills-cursor`
    is excluded by construction — it is never one of these four roots."""
    roots = [config_root / "skills"] + [
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
) -> None:
    """Routes through Task 4's precedence resolvers with explicitly named
    endpoint directories (never a root reconstructed from a directory
    basename): commands last-wins ending in personal `.cursor`, subagents
    first-wins starting with project `.cursor` — each resolver called once,
    over the full project+personal directory order, matching
    `tools/cursor_commands.py`/`tools/cursor_subagents.py`'s own
    `resolve_endpoint` docstring examples.
    """
    command_dirs: list[Path] = []
    agent_dirs: list[Path] = []
    if project_root is not None:
        command_dirs += [
            project_root / ".claude" / "commands",
            project_root / ".cursor" / "commands",
        ]
        agent_dirs += [project_root / ".cursor" / "agents", project_root / ".claude" / "agents"]
    command_dirs += [home / ".claude" / "commands", config_root / "commands"]
    agent_dirs += [config_root / "agents", home / ".claude" / "agents"]

    for resolved in cursor_commands.resolve_endpoint(command_dirs):
        _emit_command_agent(
            graph, parent, resolved.file_path, resolved.refs, "command", [], normalize
        )
    for resolved in cursor_subagents.resolve_endpoint(agent_dirs):
        _emit_command_agent(
            graph, parent, resolved.file_path, resolved.refs, "agent", [], normalize
        )


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
    realized_roots = _realize_plugins(
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


def _realize_plugins(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize,
    *,
    include_gitignored: bool,
    root_dir: Path,
    root_spec,
) -> list[Path]:
    """Realize every qualifying plugin root under `directory`, once each.

    `find_plugin_roots` already collapses same-directory candidates to one
    entry (ADR-0053's first-qualifying-candidate precedence via
    `_resolve_plugin_format`), which is what keeps a directory carrying BOTH
    `.cursor-plugin/plugin.json` and a root `plugin.json` from realizing
    twice (the single-parent hazard this task's brief calls out).

    The remaining hazard is a NESTED one: an Agent Plugins bundle's own
    fixture content (e.g. `examples/demo/plugin.json`, a DIFFERENT directory
    than any native root) must not realize as an independent bundle when it
    sits strictly below an already-realized native (`.cursor-plugin` or
    reused `.claude-plugin`) root. Filtered here before realization, so its
    directory is never excluded from sibling discovery either — matching the
    "malformed manifest doesn't own its dir" rule for the non-agent-plugins
    branch below.
    """
    candidates = find_plugin_roots(directory, CURSOR_SURFACE, include_gitignored=include_gitignored)
    native_roots = [root for root, fmt in candidates if fmt is not AGENT_PLUGINS_FORMAT]
    native_resolved = [r.resolve() for r in native_roots]

    def _strictly_below_native(root: Path) -> bool:
        resolved = root.resolve()
        return any(
            resolved != other and resolved.is_relative_to(other) for other in native_resolved
        )

    realized: list[Path] = []
    for root, fmt in candidates:
        if fmt is AGENT_PLUGINS_FORMAT and _strictly_below_native(root):
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
    return realized


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
    case, matching `descend_into_plugin`'s own contract.
    """
    manifest = plugin_root / "plugin.json"
    refs = safe_parse(graph, agent_plugins.parse, manifest)
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
) -> None:
    """Route through Task 4's precedence resolvers rather than walking
    `.cursor/commands`/`.claude/commands`/`.cursor/agents`/`.claude/agents`
    directly: `tools.cursor_commands`/`tools.cursor_subagents` already
    implement the (opposite-direction) precedence rules and containment
    checks those surfaces need.

    Neither resolver is gitignore-aware itself (both walk with unrestricted
    `Path.rglob`), so a resolved file's path is checked against the same
    `ignore_context`/`is_ignored_under` gate `_add_cursor_skills`/
    `_add_scoped_mcps` use, before it is ever emitted into the graph.
    """
    exclude_resolved = [p.resolve() for p in exclude_under]
    eval_root, spec = ignore_context(directory, include_gitignored, root_dir, root_spec)

    for resolved in cursor_commands.resolve_repo(directory):
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
    for resolved in cursor_subagents.resolve_repo(directory):
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
) -> None:
    """One command/subagent file → one self node (child of `parent`) plus,
    for subagents, any frontmatter `mcpServers`/`hooks` children `parse_file`
    returned after the self ref (parity with the `.md` branch of
    `tools.graph_build._add_repo_standalone_components`).

    `eval_root`/`spec` are `None` for the installed-mode caller (no gitignore
    filtering applies there); the declared-mode caller always passes both.
    """
    if not refs:
        return
    try:
        resolved = file_path.resolve()
    except (OSError, RuntimeError):
        return
    if any(resolved.is_relative_to(root) for root in exclude_resolved):
        return
    if eval_root is not None and is_ignored_under(resolved, eval_root, spec):
        return
    self_node = Node(key=occurrence_key(refs[0], normalize), kind=kind, ref=refs[0])
    add_child(graph, parent, self_node)
    for child_ref in refs[1:]:
        child_kind = component_type_of(child_ref)
        if not isinstance(child_kind, str):
            continue
        child_node = Node(key=occurrence_key(child_ref, normalize), kind=child_kind, ref=child_ref)
        add_child(graph, self_node, child_node)
