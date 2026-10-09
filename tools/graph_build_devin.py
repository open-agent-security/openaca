"""Devin CLI's composition builder (ADR-0070).

Devin's own files use formats OpenACA already parses, so this module owns only
the walk: which paths Devin reads, which `read_config_from` switch gates each
import, how Devin's own MCP layers merge, and which files belong to another
runtime. Content is read by the shared parsers, and the graph is built from the
same public primitives every other kind's composer uses (ADR-0053).

The surfaces are those of `docs/specs/devin-cli-agent-kind.md` "Where each
surface loads from". Three rules run through all of them:

- **Imports are composition, gated by `read_config_from`.** Each import is
  judged from the directory that holds it (`devin_config.ImportSwitches`).
- **Devin's own MCP layers merge by name, the higher layer winning**:
  project-local over project over user. Within one layer the dedicated
  `mcp_config.json` and the legacy `mcpServers` of `config.json` are both
  recorded, because their order is unverified.
- **An imported MCP server is never merged** with a same-named native one;
  each is its own occurrence with its own `source_manifest`, since Devin's
  order between them is stated nowhere.

Never imports `tools.agent_kinds` (ADR-0044's one-way dependency): the kind
module resolves the roots and passes them in.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.graph import Graph, Node
from tools.graph_build import (
    add_child,
    add_dep_manifest_packages,
    add_skill_node,
    component_type_of,
    finalize_graph,
    make_normalizer,
    occurrence_key,
    safe_parse,
    same_path,
)
from tools.graph_build_cursor import realize_plugins
from tools.parsers import claude_command_agent, devin_config, hooks_json
from tools.parsers.devin_config import (
    CONFIG_FILENAME,
    HOOKS_FILENAME,
    IMPORT_CLAUDE,
    IMPORT_COPILOT,
    IMPORT_CURSOR,
    IMPORT_WINDSURF,
    LOCAL_CONFIG_FILENAME,
    LOCAL_MCP_CONFIG_FILENAME,
    MCP_CONFIG_FILENAME,
    PROJECT_CONFIG_DIR,
    ImportSwitches,
)
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.repo_surface import DEVIN_AGENT_DIRECTORY_FILENAMES, DEVIN_PLUGIN_NAME, DEVIN_SURFACE

# Devin's own and the shared skill roots: `<root>/<name>/SKILL.md`, one level.
_NATIVE_SKILL_DIRS = frozenset({".devin", ".cognition", ".agents"})
# Windsurf's project skills are documented one level deep as well.
_WINDSURF_SKILL_DIR = ".windsurf"
# Claude Code's and Copilot's skill imports are documented as `**/SKILL.md`.
_RECURSIVE_SKILL_IMPORTS = {".claude": IMPORT_CLAUDE, ".github": IMPORT_COPILOT}
# Subagent roots: `.devin/agents/` and the shared `.agents/agents/` (ADR-0072).
_AGENT_DIRS = frozenset({".devin", ".agents"})
# A directory-form subagent's file, in Devin's documented precedence. The
# canonical copy lives in `tools/repo_surface.py`, shared with the plugin
# bundled-agent walk.
_AGENT_FILENAMES = DEVIN_AGENT_DIRECTORY_FILENAMES
# Only the stable Windsurf channel is read (spec: "Out of the first pass").
_WINDSURF_CHANNEL = "windsurf"

# Devin's MCP layer ranks; a higher rank wins a same-named server.
_USER, _PROJECT, _LOCAL = 0, 1, 2

# Devin's documented plugin-name rule: lowercase alphanumerics separated by a
# single `-` or `.`. Used to accept a store lockfile entry's name, so an
# identity of an unverified shape (`owner/repo`, a URL) can never reach a
# `component_identity`, where two `/` would mint a cross-BOM identity. The
# canonical copy lives in `tools/repo_surface.py`, shared with the declared
# manifest's own qualification test.
_PLUGIN_NAME = DEVIN_PLUGIN_NAME


@dataclass(frozen=True)
class _McpSource:
    path: Path
    # Devin's own layer, or `None` for an import, which is never merged.
    rank: int | None
    # Whether the owner's format allows a bare `{name: server}` root.
    allow_flat: bool = False


class _Files:
    """Read each Devin-read file once, recording a failure once."""

    def __init__(self, graph: Graph) -> None:
        self._graph = graph
        self._cache: dict[Path, dict | None] = {}

    def data(self, path: Path) -> dict | None:
        if path not in self._cache:
            try:
                self._cache[path] = devin_config.load(path)
            except (OSError, ValueError) as exc:
                self._graph.record_gap(f"could not parse {path}: {exc}")
                self._cache[path] = None
        return self._cache[path]


# --- Declared (repo) ---------------------------------------------------------


def build_devin_declared_graph(
    agent,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Compose a Devin CLI agent a repository declares."""
    scan_root = Path(agent.scan_root)
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = make_normalizer("repo", scan_root, scan_root, None, agent.root_label)
    root_spec = None if include_gitignored else load_gitignore_spec(scan_root)

    realized, _plugin_commands = realize_plugins(
        graph,
        root,
        scan_root,
        normalize,
        include_gitignored=include_gitignored,
        root_dir=scan_root,
        root_spec=root_spec,
        surface=DEVIN_SURFACE,
    )
    owned = [path.resolve() for path in realized]
    switches = ImportSwitches(stop_at=scan_root)
    files = _Files(graph)
    seen_skills: set[Path] = set()
    mcp_groups: dict[Path, list[_McpSource]] = {}
    agent_dirs: dict[Path, set[str]] = {}

    for path in iter_unignored_files(scan_root, root_spec):
        if _owned(path, owned):
            continue
        parts = path.relative_to(scan_root).parts
        gate = _declared_skill_gate(parts) if path.name == "SKILL.md" else None
        if gate is not None:
            owner_index, tool = gate
            if tool is None or switches.enabled(scan_root.joinpath(*parts[:owner_index]), tool):
                _add_skill(
                    graph,
                    root,
                    path,
                    normalize,
                    seen_skills,
                    root_dir=scan_root,
                    root_spec=root_spec,
                )
            continue
        if _is_claude_command(parts):
            owner = scan_root.joinpath(*parts[: _index_of_pair(parts, ".claude", "commands")])
            if switches.enabled(owner, IMPORT_CLAUDE):
                _add_markdown(graph, root, path, "command", normalize)
            continue
        if _is_flat_agent(parts):
            _add_markdown(graph, root, path, "agent", normalize, name_fallback=path.stem)
            continue
        if _is_directory_agent(parts):
            agent_dirs.setdefault(path.parent, set()).add(path.name)
            continue
        _classify_declared_config(
            graph, root, path, parts, scan_root, switches, files, mcp_groups, normalize
        )

    for agent_dir in sorted(agent_dirs):
        _add_directory_agent(graph, root, agent_dir, agent_dirs[agent_dir], normalize)
    for directory in sorted(mcp_groups):
        _add_mcp_servers(graph, root, files, mcp_groups[directory], normalize)

    # The scan root's own dependency manifests, unless the scan root is itself
    # a realized plugin, which already owns them (parity with Cursor).
    if not any(same_path(scan_root, path) for path in realized):
        add_dep_manifest_packages(
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


def _owned(path: Path, owned: list[Path]) -> bool:
    if not owned:
        return False
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return True
    return any(resolved.is_relative_to(root) for root in owned)


def _declared_skill_gate(parts: tuple[str, ...]) -> tuple[int, str | None] | None:
    """`(index of the directory owning the skill root, gating switch)` for a
    `SKILL.md` Devin loads, or `None`. A native root's switch is `None`."""
    if len(parts) >= 4 and parts[-3] == "skills":
        if parts[-4] in _NATIVE_SKILL_DIRS:
            return len(parts) - 4, None
        if parts[-4] == _WINDSURF_SKILL_DIR:
            return len(parts) - 4, IMPORT_WINDSURF
    # At least one directory between `skills/` and `SKILL.md`, as every other
    # skill root requires.
    for index in range(len(parts) - 4, -1, -1):
        tool = _RECURSIVE_SKILL_IMPORTS.get(parts[index])
        if tool is not None and parts[index + 1] == "skills":
            return index, tool
    return None


def _index_of_pair(parts: tuple[str, ...], first: str, second: str) -> int:
    for index in range(len(parts) - 2, -1, -1):
        if parts[index] == first and parts[index + 1] == second:
            return index
    return -1


def _is_claude_command(parts: tuple[str, ...]) -> bool:
    if not parts[-1].endswith(".md"):
        return False
    index = _index_of_pair(parts, ".claude", "commands")
    return index != -1 and index + 2 < len(parts)


def _is_flat_agent(parts: tuple[str, ...]) -> bool:
    return (
        len(parts) >= 3
        and parts[-2] == "agents"
        and parts[-3] in _AGENT_DIRS
        and Path(parts[-1]).suffix == ".md"
    )


def _is_directory_agent(parts: tuple[str, ...]) -> bool:
    return (
        len(parts) >= 4
        and parts[-3] == "agents"
        and parts[-4] in _AGENT_DIRS
        and parts[-1] in _AGENT_FILENAMES
    )


def _classify_declared_config(
    graph: Graph,
    root: Node,
    path: Path,
    parts: tuple[str, ...],
    scan_root: Path,
    switches: ImportSwitches,
    files: _Files,
    mcp_groups: dict[Path, list[_McpSource]],
    normalize,
) -> None:
    """Route one Devin config file, or one imported MCP/settings file, to the
    surface it feeds. `project` is the directory the file configures."""
    tail = parts[-2:]
    if len(parts) >= 2 and tail[0] == PROJECT_CONFIG_DIR:
        project = scan_root.joinpath(*parts[:-2])
        name = tail[1]
        if name == HOOKS_FILENAME:
            _add_v1_hooks(graph, root, path, "project", normalize)
        elif name in (MCP_CONFIG_FILENAME, LOCAL_MCP_CONFIG_FILENAME):
            rank = _LOCAL if name == LOCAL_MCP_CONFIG_FILENAME else _PROJECT
            mcp_groups.setdefault(project, []).append(_McpSource(path, rank))
        elif name in (CONFIG_FILENAME, LOCAL_CONFIG_FILENAME):
            local = name == LOCAL_CONFIG_FILENAME
            mcp_groups.setdefault(project, []).append(
                _McpSource(path, _LOCAL if local else _PROJECT)
            )
            _add_settings_hooks(
                graph, root, files, path, "local" if local else "project", normalize
            )
        return
    if path.name == ".mcp.json":
        if switches.enabled(path.parent, IMPORT_CLAUDE):
            mcp_groups.setdefault(path.parent, []).append(_McpSource(path, None, allow_flat=True))
        return
    if len(parts) >= 2 and tail in (
        (".claude", "settings.json"),
        (".claude", "settings.local.json"),
    ):
        project = scan_root.joinpath(*parts[:-2])
        if switches.enabled(project, IMPORT_CLAUDE):
            mcp_groups.setdefault(project, []).append(_McpSource(path, None))
            scope = "local" if tail[1] == "settings.local.json" else "project"
            _add_settings_hooks(graph, root, files, path, scope, normalize)
        return
    if len(parts) >= 2 and tail == (".cursor", "mcp.json"):
        project = scan_root.joinpath(*parts[:-2])
        if switches.enabled(project, IMPORT_CURSOR):
            mcp_groups.setdefault(project, []).append(_McpSource(path, None))


# --- Installed ---------------------------------------------------------------


def build_devin_installed_graph(
    agent,
    *,
    data_root: Path,
    legacy_config_root: Path,
    home: Path,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Compose an installed Devin CLI agent.

    `agent.config_root` is `$XDG_CONFIG_HOME/devin` (else `~/.config/devin`),
    `data_root` the plugin store's `$XDG_DATA_HOME/devin/cli`, and
    `legacy_config_root` the pre-rename `$XDG_CONFIG_HOME/cognition`. None is
    relocated by `--config-dir`, which Devin refuses (ADR-0071).
    """
    config_root = Path(agent.config_root)
    project = Path(agent.project_root) if agent.project_root is not None else None
    # Every directory whose `.devin/` a Devin run in `project` loads, nearest
    # first, up to the repository root (devin_config.project_layers).
    layers = devin_config.project_layers(project) if project is not None else []
    top = layers[-1] if layers else None
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = make_normalizer(
        "endpoint",
        config_root,
        config_root,
        # Keyed from the repository root, so an ancestor layer's file keys
        # under `project/` like the nested project's own, never absolute.
        top,
        agent.root_label,
        extra_roots=_installed_root_labels(config_root, data_root, legacy_config_root, home),
    )
    switches = ImportSwitches(stop_at=top, user_config=config_root / CONFIG_FILENAME)

    def on(tool: str) -> bool:
        return switches.enabled(project, tool)

    files = _Files(graph)

    _add_store_plugins(graph, root, data_root, normalize)
    _add_installed_skills(
        graph, root, config_root, legacy_config_root, home, project, layers, on, normalize
    )
    _add_mcp_servers(
        graph,
        root,
        files,
        _installed_mcp_sources(config_root, home, project, layers, on),
        normalize,
    )
    for agents_dir in (
        *(
            d
            for layer in layers
            for d in (layer / ".devin" / "agents", layer / ".agents" / "agents")
        ),
        config_root / "agents",
    ):
        _add_agents_dir(graph, root, agents_dir, normalize)

    for layer in layers:
        _add_v1_hooks(
            graph, root, layer / PROJECT_CONFIG_DIR / HOOKS_FILENAME, "project", normalize
        )
    for path, scope in _installed_settings_hook_sources(config_root, home, project, layers, on):
        _add_settings_hooks(graph, root, files, path, scope, normalize)
    if project is not None and on(IMPORT_CLAUDE):
        commands_dir = project / ".claude" / "commands"
        if commands_dir.is_dir():
            for md_path in sorted(commands_dir.rglob("*.md")):
                if md_path.is_file():
                    _add_markdown(graph, root, md_path, "command", normalize)

    return finalize_graph(
        graph,
        config_root,
        normalize,
        project_root=project,
        include_gitignored=include_gitignored,
        attach_include_gitignored=True,
        root_dir=None,
        root_spec=None,
        warnings=warnings,
    )


def _installed_root_labels(
    config_root: Path, data_root: Path, legacy_config_root: Path, home: Path
) -> tuple[tuple[str, Path], ...]:
    """A label for every root the installed walk reads outside the project.

    The normalizer checks the longest root first, so the bare `home` label is
    the last resort for a file directly under home (`~/.claude.json`). Devin's
    own roots are listed explicitly so a config root beneath home keeps its own
    label rather than falling to `home`. `~/.claude` keys exactly as Cursor's
    and Claude Code's own scans key it.

    The legacy `cognition` root is labelled only when it is a directory of its
    own. When it is the symlink the rename left behind, it resolves to the
    config root, and the normalizer's resolved-path fallback would otherwise
    key Devin's own files under the legacy label.
    """
    legacy = ()
    if not _same_directory(legacy_config_root, config_root):
        legacy = (("devin-cli-legacy", legacy_config_root),)
    return (
        ("devin-cli", config_root),
        ("devin-cli-data", data_root),
        *legacy,
        ("agents", home / ".agents"),
        ("claude-code", home / ".claude"),
        ("windsurf", home / ".codeium"),
        ("copilot", home / ".copilot"),
        ("home", home),
    )


def _same_directory(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except (OSError, RuntimeError):
        return False


def _installed_mcp_sources(
    config_root: Path, home: Path, project: Path | None, layers: list[Path], on
) -> list[_McpSource]:
    sources = [
        _McpSource(config_root / MCP_CONFIG_FILENAME, _USER),
        _McpSource(config_root / CONFIG_FILENAME, _USER),
    ]
    # Ranked so a nested layer outranks its ancestors, and within a layer the
    # project-local files outrank the project ones: the farthest layer takes
    # `_PROJECT`/`_LOCAL`, each nearer one the next pair above.
    for depth, layer in enumerate(reversed(layers)):
        devin = layer / PROJECT_CONFIG_DIR
        project_rank, local_rank = _PROJECT + 2 * depth, _LOCAL + 2 * depth
        sources += [
            _McpSource(devin / MCP_CONFIG_FILENAME, project_rank),
            _McpSource(devin / CONFIG_FILENAME, project_rank),
            _McpSource(devin / LOCAL_MCP_CONFIG_FILENAME, local_rank),
            _McpSource(devin / LOCAL_CONFIG_FILENAME, local_rank),
        ]
    if on(IMPORT_CLAUDE):
        if project is not None:
            sources += [
                _McpSource(project / ".mcp.json", None, allow_flat=True),
                _McpSource(project / ".claude" / "settings.json", None),
                _McpSource(project / ".claude" / "settings.local.json", None),
            ]
        sources += [
            _McpSource(home / ".claude.json", None),
            _McpSource(home / ".claude" / "settings.json", None),
            _McpSource(home / ".claude" / "settings.local.json", None),
            _McpSource(home / ".claude" / "mcp_servers.json", None, allow_flat=True),
        ]
    if on(IMPORT_CURSOR) and project is not None:
        sources.append(_McpSource(project / ".cursor" / "mcp.json", None))
    if on(IMPORT_WINDSURF):
        sources.append(_McpSource(home / ".codeium" / _WINDSURF_CHANNEL / "mcp_config.json", None))
    return sources


def _installed_settings_hook_sources(
    config_root: Path, home: Path, project: Path | None, layers: list[Path], on
) -> list[tuple[Path, str]]:
    """`hooks` blocks in settings-shaped files. Hooks are collected from every
    source and all run, so nothing here merges (docs: Configuration
    Precedence). Claude Code's are gated by `read_config_from.claude`, which
    Devin's hooks docs state outright."""
    sources: list[tuple[Path, str]] = []
    for layer in layers:
        devin = layer / PROJECT_CONFIG_DIR
        sources += [(devin / CONFIG_FILENAME, "project"), (devin / LOCAL_CONFIG_FILENAME, "local")]
    sources.append((config_root / CONFIG_FILENAME, "user"))
    if on(IMPORT_CLAUDE):
        if project is not None:
            sources += [
                (project / ".claude" / "settings.json", "project"),
                (project / ".claude" / "settings.local.json", "local"),
            ]
        sources += [
            (home / ".claude.json", "user"),
            (home / ".claude" / "settings.json", "user"),
            (home / ".claude" / "settings.local.json", "user"),
        ]
    return sources


def _add_installed_skills(
    graph: Graph,
    root: Node,
    config_root: Path,
    legacy_config_root: Path,
    home: Path,
    project: Path | None,
    layers: list[Path],
    on,
    normalize,
) -> None:
    """Every skill root, project first. Devin's own roots are read in every
    project layer, nearest first; imports from the project itself. Devin's
    own root precedes the legacy `cognition` one, so when the legacy path is
    the symlink the rename left behind, the skill keys under Devin's own
    label."""
    roots: list[tuple[Path, bool, str | None]] = [
        (layer / name / "skills", False, None)
        for layer in layers
        for name in (".devin", ".cognition", ".agents")
    ]
    if project is not None:
        roots += [
            (project / ".windsurf" / "skills", False, IMPORT_WINDSURF),
            (project / ".claude" / "skills", True, IMPORT_CLAUDE),
            (project / ".github" / "skills", True, IMPORT_COPILOT),
        ]
    roots += [
        (config_root / "skills", False, None),
        (legacy_config_root / "skills", False, None),
        (home / ".agents" / "skills", False, None),
        (home / ".codeium" / _WINDSURF_CHANNEL / "skills", False, IMPORT_WINDSURF),
        (home / ".copilot" / "skills", True, IMPORT_COPILOT),
    ]
    seen: set[Path] = set()
    for skills_dir, recursive, gate in roots:
        if gate is not None and not on(gate):
            continue
        if not skills_dir.is_dir():
            continue
        skill_files = (
            _recursive_skill_files(skills_dir) if recursive else _skill_files(graph, skills_dir)
        )
        for skill_md in skill_files:
            _add_skill(graph, root, skill_md, normalize, seen, project_root=project, stamp=True)


def _skill_files(graph: Graph, skills_dir: Path) -> list[Path]:
    """`<skills_dir>/<name>/SKILL.md`, following a symlinked skill directory."""
    try:
        children = sorted(skills_dir.iterdir())
    except OSError as exc:
        graph.record_gap(f"could not list {skills_dir}: {exc}")
        return []
    return [
        child / "SKILL.md"
        for child in children
        if not child.name.startswith(".") and child.is_dir() and (child / "SKILL.md").is_file()
    ]


def _recursive_skill_files(skills_dir: Path) -> list[Path]:
    return [path for path in iter_unignored_files(skills_dir, None) if path.name == "SKILL.md"]


def _add_store_plugins(graph: Graph, root: Node, data_root: Path, normalize) -> None:
    """Plugins in Devin's store, presence-only, from `<data>/plugins/lock.json`.

    The bundle layout inside the store could not be observed without a
    signed-in account (spec, Plugins), so neither contents nor enable state is
    claimed. A `resolved` entry yields a plugin when it carries a name that
    satisfies Devin's documented plugin-name rule; the entry shape itself is
    unverified, so any other entry lowers coverage rather than vanishing.
    """
    lock = data_root / "plugins" / "lock.json"
    if not lock.is_file():
        return
    try:
        data = devin_config.load(lock)
    except (OSError, ValueError) as exc:
        graph.record_gap(f"could not parse {lock}: {exc}")
        return
    resolved = data.get("resolved")
    if not isinstance(resolved, list):
        graph.record_gap(f"could not parse {lock}: resolved must be an array")
        return
    for index, entry in enumerate(resolved):
        name = _store_plugin_name(entry)
        if name is None:
            graph.record_gap(f"could not read a plugin name from {lock} $.resolved[{index}]")
            continue
        ref = ComponentRef(
            name=name,
            component_identity=f"plugin/{name}",
            source_manifest=str(lock),
            source_locator=f"$.resolved[{index}]",
            extra={"component_type": "plugin"},
        )
        add_child(graph, root, Node(key=occurrence_key(ref, normalize), kind="plugin", ref=ref))


def _store_plugin_name(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    for key in ("name", "identity"):
        value = entry.get(key)
        if isinstance(value, str) and _PLUGIN_NAME.fullmatch(value):
            return value
    return None


# --- Shared emission -----------------------------------------------------------


def _add_skill(
    graph: Graph,
    parent: Node,
    skill_md: Path,
    normalize,
    seen: set[Path],
    *,
    project_root: Path | None = None,
    stamp: bool = False,
    root_dir: Path | None = None,
    root_spec=None,
) -> None:
    """One skill per file, however many roots reach it: the legacy
    `cognition` root and a symlinked skill directory can both lead to a file
    another root already composed."""
    try:
        resolved = skill_md.resolve()
    except (OSError, RuntimeError):
        resolved = skill_md
    if resolved in seen:
        return
    seen.add(resolved)
    add_skill_node(
        graph,
        parent,
        skill_md.parent,
        normalize=normalize,
        project_root=project_root,
        stamp_provenance=stamp,
        root_dir=root_dir,
        root_spec=root_spec,
    )


def _add_markdown(
    graph: Graph,
    parent: Node,
    md_path: Path,
    kind: claude_command_agent.Kind,
    normalize,
    *,
    name_fallback: str | None = None,
) -> None:
    """A command or subagent file, as its own component only: Devin documents
    no `mcpServers` or `hooks` field in a subagent's frontmatter."""
    refs = claude_command_agent.parse_file(md_path, kind=kind, name_fallback=name_fallback)
    if refs:
        add_child(
            graph, parent, Node(key=occurrence_key(refs[0], normalize), kind=kind, ref=refs[0])
        )


def _add_directory_agent(
    graph: Graph, parent: Node, agent_dir: Path, present: Iterable[str], normalize
) -> None:
    for filename in _AGENT_FILENAMES:
        if filename in present:
            _add_markdown(
                graph,
                parent,
                agent_dir / filename,
                "agent",
                normalize,
                name_fallback=agent_dir.name,
            )
            return


def _add_agents_dir(graph: Graph, parent: Node, agents_dir: Path, normalize) -> None:
    """`<name>.md`, or `<name>/AGENT.md` and its documented fallbacks."""
    if not agents_dir.is_dir():
        return
    try:
        entries = sorted(agents_dir.iterdir())
    except OSError as exc:
        graph.record_gap(f"could not list {agents_dir}: {exc}")
        return
    for entry in entries:
        if entry.is_file() and entry.suffix == ".md":
            _add_markdown(graph, parent, entry, "agent", normalize, name_fallback=entry.stem)
        elif entry.is_dir():
            present = [name for name in _AGENT_FILENAMES if (entry / name).is_file()]
            _add_directory_agent(graph, parent, entry, present, normalize)


def _add_mcp_servers(
    graph: Graph, parent: Node, files: _Files, sources: list[_McpSource], normalize
) -> None:
    """Imports become occurrences as read. Devin's own layers merge by name:
    for each name only the highest layer declaring it survives, with every
    occurrence at that layer kept."""
    native: dict[str, list[tuple[int, Path, str, object]]] = {}
    for source in sources:
        if not source.path.is_file():
            continue
        data = files.data(source.path)
        if data is None:
            continue
        try:
            found = devin_config.server_map(data, allow_flat=source.allow_flat)
        except ValueError as exc:
            graph.record_gap(f"could not parse {source.path}: {exc}")
            continue
        if found is None:
            continue
        servers, prefix = found
        for name, entry in servers.items():
            if not isinstance(name, str):
                graph.record_gap(f"could not parse {source.path}: MCP server names must be strings")
                continue
            if source.rank is None:
                _emit_server(graph, parent, source.path, name, entry, prefix, normalize)
            else:
                native.setdefault(name, []).append((source.rank, source.path, prefix, entry))
    for name in sorted(native):
        winning = max(rank for rank, *_rest in native[name])
        for rank, path, prefix, entry in native[name]:
            if rank == winning:
                _emit_server(graph, parent, path, name, entry, prefix, normalize)


def _emit_server(
    graph: Graph, parent: Node, path: Path, name: str, entry: object, prefix: str, normalize
) -> None:
    try:
        refs = devin_config.parse_server(
            name, entry, source_manifest=str(path), locator_prefix=prefix
        )
    except ValueError as exc:
        graph.record_gap(f"could not parse {path}: {exc}")
        return
    for ref in refs:
        if component_type_of(ref) == "mcp_server":
            add_child(
                graph, parent, Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
            )


def _add_settings_hooks(
    graph: Graph, parent: Node, files: _Files, path: Path, scope: str, normalize
) -> None:
    if not path.is_file():
        return
    data = files.data(path)
    if data is None or "hooks" not in data:
        return
    try:
        refs = hooks_json.parse_settings_hooks(path, data["hooks"], scope=scope, strict=True)
    except ValueError as exc:
        graph.record_gap(f"could not parse {path} hooks: {exc}")
        return
    _add_hook_refs(graph, parent, refs, normalize)


def _add_v1_hooks(graph: Graph, parent: Node, path: Path, scope: str, normalize) -> None:
    if not path.is_file():
        return
    refs = safe_parse(
        graph,
        lambda hooks_path: hooks_json.parse_whole_file_hooks(hooks_path, scope=scope, strict=True),
        path,
    )
    _add_hook_refs(graph, parent, refs, normalize)


def _add_hook_refs(graph: Graph, parent: Node, refs: list[ComponentRef], normalize) -> None:
    for ref in refs:
        kind = component_type_of(ref)
        if isinstance(kind, str):
            add_child(graph, parent, Node(key=occurrence_key(ref, normalize), kind=kind, ref=ref))
