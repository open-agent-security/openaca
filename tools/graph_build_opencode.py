"""OpenCode's composition builder (ADR-0083).

OpenCode merges its configuration files and configuration directories in one
order (`Config.loadInstanceState`). This module states that order as a list of
steps, a configuration file or a configuration directory, and composes from the
steps in turn:

- **MCP servers merge by name, deep**, as OpenCode merges them. One component
  per name, with `enabled` always stated (ADR-0055).
- **Plugins are one per package name**, or one per file for a local plugin; the
  later declaration wins (`deduplicatePluginOrigins`).
- **Agents and commands** are one component per definition. A name whose merged
  definition sets `disable: true` is not loaded, and gives none.
- **Skills** come from every skill root, one per file.

The installed walk takes the steps from the roots; the declared walk takes them
from the files a repository holds, one group per configured directory.

Never imports `tools.agent_kinds` (ADR-0044's one-way dependency): the kind
module resolves the roots and passes them in.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.graph import Graph, Node
from tools.graph_build import (
    add_child,
    add_dep_manifest_packages,
    add_skill_node,
    finalize_graph,
    make_normalizer,
    occurrence_key,
)
from tools.parsers import claude_command_agent, opencode_config
from tools.parsers.claude_skill import _extract_frontmatter
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.parsers.opencode_config import (
    AGENT_DIRS,
    COMMAND_DIRS,
    CONFIG_DIR,
    EXTERNAL_SKILL_OWNERS,
    GLOBAL_FILENAMES,
    MODE_DIRS,
    PLUGIN_DIRS,
    PLUGIN_SUFFIXES,
    PROJECT_FILENAMES,
    SKILL_DIRS,
    Definition,
)

# The shared skill root that is evidence for every kind reading it (ADR-0058).
# `.claude/skills` is Claude Code's, and composition only.
SHARED_SKILL_OWNER = ".agents"


@dataclass
class _DirFiles:
    """What one configuration directory holds, in OpenCode's load order:
    commands, agents, modes, then plugin files. Skills are listed apart."""

    commands: list[tuple[Path, str]] = field(default_factory=list)
    agents: list[tuple[Path, str]] = field(default_factory=list)
    modes: list[tuple[Path, str]] = field(default_factory=list)
    plugins: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class _ConfigFile:
    path: Path


@dataclass(frozen=True)
class _ConfigDir:
    path: Path
    files: _DirFiles


_Step = _ConfigFile | _ConfigDir


class _Files:
    """Read each configuration file once, recording a failure once."""

    def __init__(self, graph: Graph) -> None:
        self._graph = graph
        self._cache: dict[Path, dict | None] = {}

    def data(self, path: Path) -> dict | None:
        if path not in self._cache:
            self._cache[path] = None
            try:
                data = opencode_config.load(path)
                # OpenCode validates a whole file before it merges any of it,
                # so one rejected entry means the file loads nothing.
                opencode_config.mcp_entries(data)
                opencode_config.plugin_specs(data)
                opencode_config.agent_definitions(data)
                opencode_config.command_definitions(data)
                self._cache[path] = data
            except (OSError, ValueError) as exc:
                self._graph.record_gap(f"could not parse {path}: {exc}")
        return self._cache[path]


# --- Declared surface classification --------------------------------------------


def declared_surface(parts: tuple[str, ...]) -> tuple[str, int] | None:
    """`(surface, index)` for a repository-relative path OpenCode reads, else
    `None`. `index` is the position of the directory that owns the file: the
    configured directory for a configuration file, `.opencode` for the files
    in it, and `.claude` or `.agents` for an external skill.

    The one classifier both evidence and declared composition use.
    """
    name = parts[-1]
    if name in PROJECT_FILENAMES:
        index = len(parts) - 2
        return "config", index - 1 if index >= 0 and parts[index] == CONFIG_DIR else index
    for k in range(len(parts) - 3, -1, -1):
        owner, sub, rest = parts[k], parts[k + 1], parts[k + 2 :]
        if owner == CONFIG_DIR:
            if sub in AGENT_DIRS and name.endswith(".md"):
                return "agent", k
            if sub in MODE_DIRS and len(rest) == 1 and name.endswith(".md"):
                return "mode", k
            if sub in COMMAND_DIRS and name.endswith(".md"):
                return "command", k
            if sub in PLUGIN_DIRS and len(rest) == 1 and Path(name).suffix in PLUGIN_SUFFIXES:
                return "plugin", k
            if sub in SKILL_DIRS and name == "SKILL.md" and not _hidden(rest[:-1]):
                return "skill", k
        if owner in EXTERNAL_SKILL_OWNERS and sub == "skills" and name == "SKILL.md":
            return "external_skill", k
    return None


def is_declared_evidence(parts: tuple[str, ...]) -> bool:
    """An OpenCode-owned file, or the shared `.agents/skills` (ADR-0058)."""
    found = declared_surface(parts)
    if found is None:
        return False
    surface, index = found
    return surface != "external_skill" or parts[index] == SHARED_SKILL_OWNER


def _hidden(parts: Iterable[str]) -> bool:
    return any(part.startswith(".") for part in parts)


# --- Declared (repo) ---------------------------------------------------------


def build_opencode_declared_graph(
    agent,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Compose an OpenCode agent a repository declares, one group per directory
    holding OpenCode configuration (docs/specs/opencode-agent-kind.md,
    Declared composition)."""
    scan_root = Path(agent.scan_root)
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = make_normalizer("repo", scan_root, scan_root, None, agent.root_label)
    root_spec = None if include_gitignored else load_gitignore_spec(scan_root)

    config_files: set[Path] = set()
    groups: set[Path] = set()
    dirs: dict[Path, _DirFiles] = {}
    skill_files: list[Path] = []
    walked: list[Path] = []
    for path in iter_unignored_files(scan_root, root_spec):
        walked.append(path)
        parts = path.relative_to(scan_root).parts
        found = declared_surface(parts)
        if found is None:
            continue
        surface, index = found
        if surface == "config":
            config_files.add(path)
            groups.add(scan_root.joinpath(*parts[: index + 1]))
            continue
        if surface in ("skill", "external_skill"):
            skill_files.append(path)
            continue
        config_dir = scan_root.joinpath(*parts[: index + 1])
        groups.add(config_dir.parent)
        _add_dir_file(dirs.setdefault(config_dir, _DirFiles()), surface, path, config_dir)

    files = _Files(graph)
    seen_skills: set[Path] = set()
    for group in sorted(groups):
        steps: list[_Step] = [
            _ConfigFile(group / name) for name in PROJECT_FILENAMES if group / name in config_files
        ]
        config_dir = group / CONFIG_DIR
        steps += [
            _ConfigFile(config_dir / name)
            for name in PROJECT_FILENAMES
            if config_dir / name in config_files
        ]
        if config_dir in dirs:
            steps.append(_ConfigDir(config_dir, dirs[config_dir]))
        merged = _compose_steps(graph, root, steps, files, normalize, cache_root=None)
        for skill_dir in _skill_path_dirs(merged, base=group, home=None):
            skill_dir = skill_dir.resolve()
            if not skill_dir.is_relative_to(scan_root.resolve()):
                continue
            # `**/SKILL.md`, with no hidden directory, as OpenCode globs it.
            skill_files += [
                p
                for p in walked
                if p.name == "SKILL.md"
                and p.resolve().is_relative_to(skill_dir)
                and not _hidden(p.resolve().relative_to(skill_dir).parts[:-1])
            ]

    for skill_md in sorted(set(skill_files)):
        _add_skill(
            graph, root, skill_md, normalize, seen_skills, root_dir=scan_root, root_spec=root_spec
        )

    add_dep_manifest_packages(
        graph, root, scan_root, normalize, root_dir=scan_root, root_spec=root_spec
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


def _add_dir_file(files: _DirFiles, surface: str, path: Path, config_dir: Path) -> None:
    relative = path.relative_to(config_dir).parts
    name = opencode_config.entry_name("/".join(relative[1:]))
    if surface == "agent":
        files.agents.append((path, name))
    elif surface == "mode":
        files.modes.append((path, name))
    elif surface == "command":
        files.commands.append((path, name))
    elif surface == "plugin":
        files.plugins.append(path)


# --- Installed ---------------------------------------------------------------


def build_opencode_installed_graph(
    agent,
    *,
    cache_root: Path,
    managed_root: Path,
    home: Path,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Compose an installed OpenCode agent.

    `agent.config_root` is `$XDG_CONFIG_HOME/opencode` (else
    `~/.config/opencode`), `cache_root` the plugin cache's
    `$XDG_CACHE_HOME/opencode`, and `managed_root` the system directory OpenCode
    merges last. None is relocated by `--config-dir`, which OpenCode refuses.
    """
    config_root = Path(agent.config_root)
    project = Path(agent.project_root).resolve() if agent.project_root is not None else None
    layers = opencode_config.project_layers(project) if project is not None else []
    top = layers[-1] if layers else None
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    files = _Files(graph)

    # `Config.loadInstanceState`, lowest precedence first.
    steps: list[_Step] = [_ConfigFile(config_root / name) for name in GLOBAL_FILENAMES]
    for layer in reversed(layers):
        steps += [_ConfigFile(layer / name) for name in PROJECT_FILENAMES]
    config_dirs = _config_directories(config_root, layers, home)
    for directory in config_dirs:
        if directory.name == CONFIG_DIR:
            steps += [_ConfigFile(directory / name) for name in PROJECT_FILENAMES]
        steps.append(_ConfigDir(directory, _scan_config_dir(directory)))
    steps += [_ConfigFile(managed_root / name) for name in PROJECT_FILENAMES]

    merged = _merged_config(steps, files)
    skill_paths = _skill_path_dirs(merged, base=project, home=home)
    normalize = make_normalizer(
        "endpoint",
        config_root,
        config_root,
        # Keyed from the repository root, so an ancestor layer's file keys
        # under `project/` like the nested project's own, never absolute.
        top,
        agent.root_label,
        extra_roots=_installed_root_labels(home, managed_root, skill_paths, top, config_root),
    )
    _compose_steps(graph, root, steps, files, normalize, cache_root=cache_root)

    seen: set[Path] = set()
    skill_roots: list[tuple[Path, bool, Path | None]] = [
        (home / owner / "skills", True, project) for owner in EXTERNAL_SKILL_OWNERS
    ]
    skill_roots += [
        (layer / owner / "skills", True, layer)
        for layer in layers
        for owner in EXTERNAL_SKILL_OWNERS
    ]
    skill_roots += [
        (directory / name, False, project) for directory in config_dirs for name in SKILL_DIRS
    ]
    skill_roots += [(directory, False, project) for directory in skill_paths]
    for skills_dir, dot, owner in skill_roots:
        for skill_md in _walk(skills_dir, dot=dot):
            if skill_md.name == "SKILL.md":
                _add_skill(graph, root, skill_md, normalize, seen, project_root=owner, stamp=True)

    return finalize_graph(
        graph,
        config_root,
        normalize,
        project_root=top,
        include_gitignored=include_gitignored,
        attach_include_gitignored=True,
        root_dir=None,
        root_spec=None,
        warnings=warnings,
    )


def _config_directories(config_root: Path, layers: list[Path], home: Path) -> list[Path]:
    """`ConfigPaths.directories`: the config root, each layer's `.opencode`
    nearest first, then `~/.opencode`, each once."""
    out: list[Path] = [config_root]
    for directory in [layer / CONFIG_DIR for layer in layers] + [home / CONFIG_DIR]:
        if directory.is_dir() and directory not in out:
            out.append(directory)
    return out


def _scan_config_dir(directory: Path) -> _DirFiles:
    """The Markdown and plugin files of one configuration directory, by
    OpenCode's globs: `{command,commands}/**/*.md`, `{agent,agents}/**/*.md`,
    `{mode,modes}/*.md` and `{plugin,plugins}/*.{ts,js}`."""
    files = _DirFiles()
    for sub in COMMAND_DIRS:
        files.commands += _markdown(directory / sub, recursive=True)
    for sub in AGENT_DIRS:
        files.agents += _markdown(directory / sub, recursive=True)
    for sub in MODE_DIRS:
        files.modes += _markdown(directory / sub, recursive=False)
    for sub in PLUGIN_DIRS:
        files.plugins += [
            path
            for path in _children(directory / sub)
            if path.is_file() and path.suffix in PLUGIN_SUFFIXES
        ]
    return files


def _markdown(base: Path, *, recursive: bool) -> list[tuple[Path, str]]:
    paths = _walk(base, dot=True) if recursive else [p for p in _children(base) if p.is_file()]
    return [
        (path, opencode_config.entry_name(path.relative_to(base).as_posix()))
        for path in paths
        if path.suffix == ".md"
    ]


def _children(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir())
    except OSError:
        return []


def _walk(base: Path, *, dot: bool) -> list[Path]:
    """Every file below `base`, following symlinked directories once, as
    OpenCode's globs do (`symlink: true`). `dot` admits hidden directories."""
    if not base.is_dir():
        return []
    found: list[Path] = []
    seen: set[str] = set()
    for current, dirnames, filenames in os.walk(base, followlinks=True):
        real = os.path.realpath(current)
        if real in seen:
            dirnames[:] = []
            continue
        seen.add(real)
        dirnames[:] = sorted(d for d in dirnames if dot or not d.startswith("."))
        found += [Path(current) / name for name in sorted(filenames)]
    return found


def _installed_root_labels(
    home: Path,
    managed_root: Path,
    skill_paths: list[Path],
    top: Path | None,
    config_root: Path,
) -> tuple[tuple[str, Path], ...]:
    """A label for every root the installed walk reads outside the project.
    `~/.claude` and `~/.agents` key as Claude Code's, Cursor's and Devin's scans
    key them. A `skills.paths` directory outside every other root takes its own
    numbered label, so no host path enters a key."""
    labels: list[tuple[str, Path]] = [
        ("claude-code", home / ".claude"),
        ("agents", home / ".agents"),
        ("opencode-managed", managed_root),
        ("home", home),
    ]
    covered = [root for _label, root in labels] + [config_root] + ([top] if top else [])
    for index, directory in enumerate(skill_paths):
        if not any(_under(directory, root) for root in covered):
            labels.append((f"opencode-skills-path-{index}", directory))
    return tuple(labels)


def _under(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


# --- Shared composition ---------------------------------------------------------


def _merged_config(steps: list[_Step], files: _Files) -> dict:
    merged: dict = {}
    for step in steps:
        if isinstance(step, _ConfigFile) and step.path.is_file():
            data = files.data(step.path)
            if data is not None:
                merged = opencode_config.merge_deep(merged, data)
    return merged


def _compose_steps(
    graph: Graph,
    root: Node,
    steps: list[_Step],
    files: _Files,
    normalize,
    *,
    cache_root: Path | None,
) -> dict:
    """Compose MCP servers, plugins, agents and commands from `steps`, in
    order. Returns the merged configuration."""
    merged: dict = {}
    servers: dict[str, list[tuple[Path, opencode_config.McpEntry]]] = {}
    plugins: dict[str, ComponentRef] = {}
    agents: list[tuple[Definition, ComponentRef]] = []
    modes: list[tuple[Definition, ComponentRef]] = []
    commands: list[ComponentRef] = []
    for step in steps:
        if isinstance(step, _ConfigDir):
            for path, name in step.files.commands:
                commands += _markdown_refs(path, "command", name)
            for path, name in step.files.agents + step.files.modes:
                agents += [
                    (_markdown_definition(path, ref), ref)
                    for ref in _markdown_refs(path, "agent", name)
                ]
            for path in step.files.plugins:
                ref = opencode_config.local_plugin_ref(path, source_manifest=str(path), locator="$")
                _set_plugin(plugins, _path_key(path), ref)
            continue
        if not step.path.is_file():
            continue
        data = files.data(step.path)
        if data is None:
            continue
        merged = opencode_config.merge_deep(merged, data)
        manifest = str(step.path)
        for name, item in opencode_config.mcp_entries(data).items():
            servers.setdefault(name, []).append((step.path, item))
        for index, spec in opencode_config.plugin_specs(data):
            found = _plugin_ref(spec, step.path, index, cache_root)
            if found is not None:
                _set_plugin(plugins, *found)
        for definition in opencode_config.agent_definitions(data):
            ref = _definition_ref(definition, "agent", manifest)
            (modes if definition.mode else agents).append((definition, ref))
        for definition in opencode_config.command_definitions(data):
            commands.append(_definition_ref(definition, "command", manifest))

    for name in sorted(servers):
        _add_server(graph, root, name, servers[name], normalize)
    for ref in plugins.values():
        _add(graph, root, ref, "plugin", normalize)
    disabled: dict[str, bool] = {}
    for definition, _ref in agents + modes:
        if definition.disable is not None:
            disabled[definition.name] = definition.disable
    for definition, ref in agents + modes:
        if not disabled.get(definition.name):
            _add(graph, root, ref, "agent", normalize)
    for ref in commands:
        _add(graph, root, ref, "command", normalize)
    for url in opencode_config.string_list(opencode_config.skills_setting(merged), "urls"):
        graph.record_gap(f"skills fetched from {url} are not inventoried")
    return merged


def _add(graph: Graph, parent: Node, ref: ComponentRef, kind: str, normalize) -> None:
    add_child(graph, parent, Node(key=occurrence_key(ref, normalize), kind=kind, ref=ref))


def _add_server(
    graph: Graph,
    parent: Node,
    name: str,
    declared: list[tuple[Path, opencode_config.McpEntry]],
    normalize,
) -> None:
    """One merged server, attributed to the highest-precedence file whose
    entry sets `type`. A merged entry with no `type` is not loaded."""
    merged: dict = {}
    owner: tuple[Path, opencode_config.McpEntry] | None = None
    for path, item in declared:
        merged = opencode_config.merge_deep(merged, item.entry)
        if item.type is not None:
            owner = (path, item)
    if owner is None or not isinstance(merged.get("type"), str):
        return
    path, item = owner
    try:
        refs = opencode_config.server_refs(
            name, merged, source_manifest=str(path), prefix=item.prefix
        )
    except ValueError as exc:
        graph.record_gap(f"could not parse {path}: {exc}")
        return
    layers = [str(p) for p, _item in declared]
    for ref in refs:
        extra = {
            **ref.extra,
            "enabled": merged.get("enabled") is not False,
            opencode_config.LAYERS_KEY: layers,
        }
        _add(graph, parent, replace(ref, extra=extra), "mcp_server", normalize)


def _set_plugin(plugins: dict[str, ComponentRef], key: str, ref: ComponentRef) -> None:
    # The later declaration wins, at its own position (`deduplicatePluginOrigins`).
    plugins.pop(key, None)
    plugins[key] = ref


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return str(path)


def _plugin_ref(
    spec: str, config_file: Path, index: int, cache_root: Path | None
) -> tuple[str, ComponentRef] | None:
    """`(dedupe key, ref)` for one `plugin` entry, or `None` for one OpenCode
    ignores. npm plugins key on the package name, local ones on their file."""
    manifest, locator = str(config_file), f"$.plugin[{index}]"
    if opencode_config.is_path_spec(spec):
        target = opencode_config.resolve_path_spec(spec, config_file)
        ref = opencode_config.local_plugin_ref(target, source_manifest=manifest, locator=locator)
        return _path_key(target), ref
    if opencode_config.is_deprecated_plugin(spec):
        return None
    registry = opencode_config.registry_spec(spec)
    extra: dict = {
        "component_type": "plugin",
        "declared_by": {"kind": "manifest", "path": manifest},
    }
    if registry is None:
        # A Git, URL or `file:` spec: presence-only. No identity, since a
        # spec with two `/` would otherwise mint a cross-BOM one.
        extra["component_path"] = [{"type": "plugin", "name": spec}]
        ref = ComponentRef(name=spec, source_manifest=manifest, source_locator=locator, extra=extra)
        return spec, ref
    name, version = registry.package, opencode_config.exact_version(registry.version)
    installed = (
        opencode_config.installed_package(cache_root, registry) if cache_root is not None else None
    )
    if installed is not None:
        name, version = installed
    extra["component_path"] = [{"type": "plugin", "name": name}]
    ref = ComponentRef(
        ecosystem="npm",
        name=name,
        version=version,
        component_identity=f"plugin/npm/{name}",
        source_manifest=manifest,
        source_locator=locator,
        extra=extra,
    )
    return registry.install_name, ref


def _definition_ref(definition: Definition, kind: str, manifest: str) -> ComponentRef:
    extra: dict = {"scope_owner": None, "component_type": kind}
    if definition.description:
        extra["description"] = definition.description
    return ComponentRef(
        name=definition.name,
        component_identity=f"claude-{kind}/{definition.name}",
        source_manifest=manifest,
        source_locator=definition.locator,
        extra=extra,
    )


def _markdown_refs(path: Path, kind: claude_command_agent.Kind, name: str) -> list[ComponentRef]:
    """The file's own component only. OpenCode reads no `mcpServers` or
    `hooks` from an agent's frontmatter."""
    return claude_command_agent.parse_file(path, kind=kind, name_fallback=name)[:1]


def _markdown_definition(path: Path, ref: ComponentRef) -> Definition:
    frontmatter = claude_command_agent._read_frontmatter(path)
    disable = frontmatter.get("disable") if isinstance(frontmatter, dict) else None
    return Definition(
        name=ref.name or "",
        locator="$",
        disable=disable if isinstance(disable, bool) else None,
    )


def _skill_path_dirs(merged: dict, *, base: Path | None, home: Path | None) -> list[Path]:
    """`skills.paths`: `~/` against `home`, a relative entry against `base`.
    An entry that cannot be resolved here is skipped, as OpenCode skips a path
    that is not a directory."""
    out: list[Path] = []
    for item in opencode_config.string_list(opencode_config.skills_setting(merged), "paths"):
        if item.startswith("~/"):
            if home is None:
                continue
            directory = home / item[2:]
        elif Path(item).is_absolute():
            directory = Path(item)
        elif base is not None:
            directory = base / item
        else:
            continue
        if directory.is_dir() and directory not in out:
            out.append(directory)
    return out


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
    """One skill per file, however many roots reach it. A `SKILL.md` without a
    string `name` does not load (`isSkillFrontmatter`). One the scan cannot
    parse goes to `add_skill_node`, which records the gap."""
    try:
        resolved = skill_md.resolve()
    except (OSError, RuntimeError):
        resolved = skill_md
    if resolved in seen:
        return
    seen.add(resolved)
    if not _loads_as_skill(skill_md):
        return
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


def _loads_as_skill(skill_md: Path) -> bool:
    """`isSkillFrontmatter`: a string `name`, and a `description` that is a
    string when present. No frontmatter means no name. A read or parse failure
    is `True`, so `add_skill_node` records it as a gap."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True
    if not text.startswith("---"):
        return False
    try:
        frontmatter = _extract_frontmatter(text, strict=True)
    except ValueError:
        return True
    assert frontmatter is not None
    if "description" in frontmatter and not isinstance(frontmatter["description"], str):
        return False
    return isinstance(frontmatter.get("name"), str)
