"""Pi's composition builder (ADR-0081, ADR-0082).

Pi's MCP servers and skills use formats OpenACA already parses, so this module
owns the walk: which directories Pi reads, how its two `mcp.json` scopes merge,
which packages a settings file declares and where Pi installed them, and which
project a trust decision lets load. The surfaces are those of
`docs/specs/pi-agent-kind.md` "Where each surface loads from".

Resources are composed in Pi's precedence order: project settings entries, the
project's own directories, user settings entries, the user's own directories,
then packages. A file reached twice is composed once, at its first route.

Never imports `tools.agent_kinds` (ADR-0044's one-way dependency): the kind
module resolves the roots and passes them in.
"""

from __future__ import annotations

import glob
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from tools.component_ref import ComponentRef
from tools.graph import Graph, Node
from tools.graph_build import (
    add_child,
    add_dep_manifest_packages,
    finalize_graph,
    is_ignored_under,
    make_normalizer,
    occurrence_key,
    safe_parse,
)
from tools.parsers import claude_command_agent, claude_skill, pi_config, skill_lock
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.parsers.pi_config import (
    MCP_FILENAME,
    PROJECT_CONFIG_DIR,
    RESOURCE_TYPES,
    SETTINGS_FILENAME,
    TRUST_FILENAME,
    PackageEntry,
    PackageSource,
)

_INDEX_FILES = ("index.ts", "index.js")
_EXTENSION_SUFFIXES = (".ts", ".js")


@dataclass(frozen=True)
class _Layer:
    scope: Literal["user", "project"]
    # Where the layer's settings entries and own directories resolve: the
    # agent directory, or `<project>/.pi`.
    base: Path
    settings_path: Path
    settings: dict
    # The cross-tool skill roots this layer reads.
    agents_skills: tuple[Path, ...] = ()
    # Which directory's `skills-lock.json` records its skills' provenance.
    owner: Path | None = None


@dataclass(frozen=True)
class _Package:
    """The package a bundled resource belongs to."""

    name: str
    path: Path


class _Composer:
    def __init__(
        self,
        graph: Graph,
        root: Node,
        normalize,
        *,
        home: Path | None,
        visible: Callable[[Path], bool],
        stamp: bool,
        install_roots: dict[str, Path] | None,
        root_dir: Path | None = None,
        root_spec=None,
    ) -> None:
        self.graph = graph
        self.root = root
        self.normalize = normalize
        self.home = home
        self.visible = visible
        self.stamp = stamp
        # `{scope: directory}` holding `npm/` and `git/` installs, or `None` in
        # a repository, where an install is never read.
        self.install_roots = install_roots
        self.root_dir = root_dir
        self.root_spec = root_spec
        self._seen: dict[str, set[Path]] = {kind: set() for kind in RESOURCE_TYPES}
        self._deps: set[Path] = set()

    # --- MCP servers ---------------------------------------------------------

    def add_mcp_servers(self, files: list[tuple[Path, Literal["user", "project"]]]) -> None:
        files = [(path, scope) for path, scope in files if self.visible(path)]
        servers = pi_config.merge_mcp_servers(
            files, record_gap=self.graph.record_gap, note=self.graph.warnings.append
        )
        for server in servers:
            for ref in pi_config.server_refs(server):
                node = Node(key=occurrence_key(ref, self.normalize), kind="mcp_server", ref=ref)
                add_child(self.graph, self.root, node)

    # --- Settings entries and a layer's own directories -----------------------

    def add_settings_resources(self, layer: _Layer) -> None:
        for resource_type in RESOURCE_TYPES:
            for entry in pi_config.resource_entries(layer.settings, resource_type):
                path = pi_config.resolve_path(entry, layer.base, self.home)
                if path is None or not self.visible(path):
                    self.graph.record_gap(
                        f"{layer.settings_path}: the {resource_type} entry {entry!r} is "
                        "outside the repository"
                    )
                    continue
                self.add_paths(self.root, resource_type, [path], owner=layer.owner)

    def add_own_resources(self, layer: _Layer) -> None:
        for entry, manifest, index in self.extension_entries(layer.base / "extensions"):
            self.add_extension(self.root, entry, manifest=manifest, index=index)
        for skills_root in (layer.base / "skills", *layer.agents_skills):
            for skill_dir in self.skill_dirs(skills_root):
                self.add_skill(self.root, skill_dir, owner=layer.owner)
        for md_path in self.markdown_files(layer.base / "prompts", recursive=False):
            self.add_prompt(self.root, md_path)

    def add_paths(
        self,
        parent: Node,
        resource_type: str,
        paths: Iterable[Path],
        *,
        package: _Package | None = None,
        owner: Path | None = None,
    ) -> None:
        """Pi's `collectFilesFromPaths`: a file is the resource, a directory is
        walked as its resource type's own directory is."""
        for path in paths:
            if not self.visible(path):
                continue
            if path.is_file():
                if resource_type == "extensions":
                    self.add_extension(parent, path, package=package)
                elif resource_type == "skills" and path.name == "SKILL.md":
                    self.add_skill(parent, path.parent, package=package, owner=owner)
                elif resource_type == "prompts" and path.suffix == ".md":
                    self.add_prompt(parent, path, package=package)
            elif path.is_dir():
                if resource_type == "extensions":
                    for entry, manifest, index in self.extension_entries(path):
                        self.add_extension(
                            parent, entry, manifest=manifest, index=index, package=package
                        )
                elif resource_type == "skills":
                    for skill_dir in self.skill_dirs(path):
                        self.add_skill(parent, skill_dir, package=package, owner=owner)
                else:
                    for md_path in self.markdown_files(path, recursive=True):
                        self.add_prompt(parent, md_path, package=package)

    # --- Discovery -------------------------------------------------------------

    def children(self, directory: Path) -> list[Path]:
        """`directory`'s entries Pi's walks visit: not `.`-prefixed, not
        `node_modules`."""
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            self.graph.record_gap(f"could not list {directory}: {exc}")
            return []
        return [
            entry
            for entry in entries
            if not entry.name.startswith(".")
            and entry.name != "node_modules"
            and self.visible(entry)
        ]

    def extension_entries(self, directory: Path) -> list[tuple[Path, Path | None, int | None]]:
        """`(entry, package.json, index)` per extension Pi's CLI loads from
        `directory`: the directory's own entries if it declares any, else each
        child file and each child directory's entries."""
        if not directory.is_dir() or not self.visible(directory):
            return []
        own = self._directory_extension_entries(directory)
        if own is not None:
            return own
        entries: list[tuple[Path, Path | None, int | None]] = []
        for child in self.children(directory):
            if child.is_file() and child.name.endswith(_EXTENSION_SUFFIXES):
                entries.append((child, None, None))
            elif child.is_dir():
                entries.extend(self._directory_extension_entries(child) or [])
        return entries

    def _directory_extension_entries(
        self, directory: Path
    ) -> list[tuple[Path, Path | None, int | None]] | None:
        package_json = directory / "package.json"
        if package_json.is_file() and self.visible(package_json):
            listed = (pi_config.pi_manifest(package_json) or {}).get("extensions") or []
            entries: list[tuple[Path, Path | None, int | None]] = [
                (path, package_json, index)
                for index, entry in enumerate(listed)
                if (path := Path(os.path.normpath(directory / entry))).exists()
                and self.visible(path)
            ]
            if entries:
                return entries
        for name in _INDEX_FILES:
            index_file = directory / name
            if index_file.is_file() and self.visible(index_file):
                return [(index_file, None, None)]
        return None

    def skill_dirs(self, directory: Path) -> list[Path]:
        """Every skill below `directory`: a directory holding `SKILL.md`,
        below which the walk does not go."""
        found: list[Path] = []
        visited: set[Path] = set()

        def walk(current: Path) -> None:
            resolved = _resolved(current)
            if resolved in visited:
                return
            visited.add(resolved)
            skill_md = current / "SKILL.md"
            if skill_md.is_file() and self.visible(skill_md):
                found.append(current)
                return
            for child in self.children(current):
                if child.is_dir():
                    walk(child)

        if directory.is_dir() and self.visible(directory):
            walk(directory)
        return found

    def markdown_files(self, directory: Path, *, recursive: bool) -> list[Path]:
        if not directory.is_dir() or not self.visible(directory):
            return []
        found: list[Path] = []
        visited: set[Path] = set()

        def walk(current: Path) -> None:
            resolved = _resolved(current)
            if resolved in visited:
                return
            visited.add(resolved)
            for child in self.children(current):
                if child.is_file() and child.suffix == ".md":
                    found.append(child)
                elif recursive and child.is_dir():
                    walk(child)

        walk(directory)
        return found

    # --- Emission ----------------------------------------------------------------

    def _first(self, resource_type: str, path: Path) -> bool:
        key = _resolved(path)
        if key in self._seen[resource_type]:
            return False
        self._seen[resource_type].add(key)
        return True

    def add_extension(
        self,
        parent: Node,
        entry: Path,
        *,
        manifest: Path | None = None,
        index: int | None = None,
        package: _Package | None = None,
    ) -> None:
        """One extension, a `plugin` (ADR-0082), named as Pi's startup listing
        names it: the entry file, or an `index` entry's directory."""
        if not self._first("extensions", entry):
            return
        if entry.name in _INDEX_FILES:
            name = entry.parent.name
        elif entry.name.endswith(_EXTENSION_SUFFIXES):
            name = entry.name.rsplit(".", 1)[0]
        else:
            name = entry.name
        source = manifest or entry
        ref = ComponentRef(
            name=name,
            source_manifest=str(source),
            source_locator=f"$.pi.extensions[{index}]" if manifest is not None else "$",
            extra={
                "component_type": "plugin",
                "declared_by": {"kind": "manifest", "path": str(source)},
                "component_path": [{"type": "plugin", "name": name}],
            },
        )
        node = self._emit(parent, _in_package(ref, package), "plugin")
        if manifest is not None:
            self.add_deps(node, manifest.parent)
        elif entry.name in _INDEX_FILES:
            self.add_deps(node, entry.parent)

    def add_skill(
        self,
        parent: Node,
        skill_dir: Path,
        *,
        package: _Package | None = None,
        owner: Path | None = None,
    ) -> None:
        skill_md = skill_dir / "SKILL.md"
        if not self._first("skills", skill_md):
            return
        refs = safe_parse(self.graph, lambda path: claude_skill.parse(path, strict=True), skill_md)
        if refs and not pi_config.skill_has_description(skill_md):
            self.graph.warnings.append(f"{skill_md}: Pi does not load a skill with no description")
            return
        for ref in refs:
            if self.stamp and ref.name:
                provenance = skill_lock.provenance_for_skill(skill_md, ref.name, project_root=owner)
                if provenance is not None:
                    ref = replace(ref, extra={**ref.extra, "source_provenance": provenance})
            node = self._emit(parent, _in_package(ref, package), "skill")
            self.add_deps(node, skill_dir)

    def add_prompt(self, parent: Node, md_path: Path, *, package: _Package | None = None) -> None:
        """A prompt template is a `/` command named by its file. Pi ignores a
        frontmatter `name`."""
        if not self._first("prompts", md_path):
            return
        refs = claude_command_agent.parse_file(md_path, kind="command")
        if refs:
            ref = replace(refs[0], name=md_path.stem)
            self._emit(parent, _in_package(ref, package), "command")

    def _emit(self, parent: Node, ref: ComponentRef, kind: str) -> Node:
        return add_child(
            self.graph, parent, Node(key=occurrence_key(ref, self.normalize), kind=kind, ref=ref)
        )

    def add_deps(self, node: Node, directory: Path) -> None:
        """`directory`'s dependency manifests, under the first component that
        claims the directory: one occurrence has one parent."""
        key = _resolved(directory)
        if key in self._deps:
            return
        self._deps.add(key)
        add_dep_manifest_packages(
            self.graph,
            node,
            directory,
            self.normalize,
            root_dir=self.root_dir,
            root_spec=self.root_spec,
        )

    # --- Packages ----------------------------------------------------------------

    def add_packages(self, layers: list[_Layer]) -> None:
        """Pi's `dedupePackages`: project entries first, keyed by identity. A
        later project entry replaces an earlier one, a user entry yields to a
        project one, and a project `autoload: false` entry filters the user
        entry, which is then the package."""
        chosen: dict[str, tuple[_Layer, PackageEntry]] = {}
        for layer in sorted(layers, key=lambda candidate: candidate.scope != "project"):
            for entry in pi_config.package_entries(
                layer.settings, path=layer.settings_path, record_gap=self.graph.record_gap
            ):
                identity = self._package_identity(layer, entry.source)
                existing = chosen.get(identity)
                if existing is None or layer.scope == "project":
                    chosen[identity] = (layer, entry)
                elif existing[0].scope == "project" and not existing[1].autoload:
                    chosen[identity] = (layer, entry)
        for layer, entry in chosen.values():
            self.add_package(layer, entry)

    def _package_identity(self, layer: _Layer, source: PackageSource) -> str:
        if source.kind == "npm":
            return f"npm:{source.name}"
        if source.kind == "git":
            return f"git:{source.host}/{source.path}"
        path = pi_config.resolve_path(source.path or "", layer.base, self.home)
        return f"local:{_resolved(path) if path is not None else source.raw}"

    def _install_path(self, layer: _Layer, source: PackageSource) -> Path | None:
        if source.kind == "local":
            return pi_config.resolve_path(source.path or "", layer.base, self.home)
        if self.install_roots is None:
            return None
        base = self.install_roots[layer.scope]
        if source.kind == "npm":
            name = source.name or ""
            if not name or ".." in name.split("/") or name.startswith("/"):
                return None
            return base / "npm" / "node_modules" / name
        return base / "git" / (source.host or "") / (source.path or "")

    def add_package(self, layer: _Layer, entry: PackageEntry) -> None:
        source = entry.source
        install = self._install_path(layer, source)
        usable = install is not None and self.visible(install)
        if source.kind == "local" and usable and install is not None:
            if install.is_file():
                # A local source naming a file is one extension.
                self.add_extension(self.root, install)
                return
            if not install.exists():
                # Pi skips a local source that does not exist and installs nothing.
                self.graph.warnings.append(
                    f"{layer.settings_path} $.packages[{entry.index}]: {install} does not exist"
                )
                return
        root = install if usable and install is not None and install.is_dir() else None
        ref = _package_ref(layer, entry, root)
        node = self._emit(self.root, ref, "plugin")
        if root is None:
            self.graph.record_gap(self._missing_package_message(layer, source, install))
            return
        self.add_deps(node, root)
        assert ref.name is not None
        package = _Package(name=ref.name, path=root / "package.json")
        manifest = pi_config.pi_manifest(root / "package.json")
        for resource_type in RESOURCE_TYPES:
            if manifest:
                paths = self._manifest_paths(root, manifest.get(resource_type) or [])
            else:
                paths = [root / resource_type]
            self.add_paths(node, resource_type, paths, package=package, owner=layer.owner)

    def _missing_package_message(
        self, layer: _Layer, source: PackageSource, install: Path | None
    ) -> str:
        where = f"{layer.settings_path} $.packages"
        if self.install_roots is None and source.kind != "local":
            return f"{where}: {source.raw!r} installs at run time; its contents are unknown"
        if self.install_roots is None and (install is None or not self.visible(install)):
            return f"{where}: {source.raw!r} is outside the repository"
        if install is None:
            return f"{where}: {source.raw!r} names no location Pi installs to"
        if source.kind == "npm" and layer.scope == "user":
            return (
                f"{where}: {source.raw!r} is not under {install.parent}; a global npm "
                "install is not read, and Pi installs a missing package at its next start"
            )
        return f"{where}: {source.raw!r} is not installed; Pi installs it at its next start"

    def _manifest_paths(self, root: Path, entries: list[str]) -> list[Path]:
        """Pi's `collectFilesFromManifestEntries`, without its `!`, `+` and `-`
        patterns: a plain entry is a path, a glob matches no `.`-prefixed
        segment."""
        paths: list[Path] = []
        for entry in entries:
            if entry.startswith(("!", "+", "-")):
                continue
            if "*" not in entry and "?" not in entry:
                paths.append(Path(os.path.normpath(root / entry)))
                continue
            matches = sorted(glob.glob(entry, root_dir=root, recursive=True))
            paths.extend(
                Path(os.path.normpath(root / match))
                for match in matches
                if not any(part.startswith(".") and part != ".." for part in Path(match).parts)
            )
        return paths


def _package_ref(layer: _Layer, entry: PackageEntry, root: Path | None) -> ComponentRef:
    """A package as a `plugin` (ADR-0082). An npm package's version is the
    installed one, else an exact declared one; a git package's is the
    checkout's commit."""
    source = entry.source
    extra: dict = {
        "component_type": "plugin",
        "declared_by": {"kind": "manifest", "path": str(layer.settings_path)},
        "scope": layer.scope,
    }
    ecosystem: str | None = None
    version: str | None = None
    identity: str | None = None
    if source.kind == "npm":
        name = source.name or source.raw
        ecosystem = "npm"
        identity = f"plugin/npm/{name}"
        installed = pi_config.package_json_field(root / "package.json", "version") if root else None
        declared = source.version if pi_config.is_exact_version(source.version) else None
        version = installed or declared
        extra["install_source"] = f"npm:{name}" + (f"@{source.version}" if source.version else "")
    elif source.kind == "git":
        host, path = source.host or "", source.path or ""
        name = f"{host}/{path}"
        identity = f"plugin/git/{name}"
        version = pi_config.git_head(root) if root else None
        if version is None and pi_config.is_commit(source.ref):
            version = (source.ref or "").lower()
        if host.lower() == "github.com" and len(path.split("/")) == 2:
            ecosystem, name = "github", path
        if source.ref and not pi_config.is_commit(source.ref):
            extra["git_ref"] = source.ref
        extra["install_source"] = f"git+https://{host}/{path}" + (
            f"@{source.ref}" if source.ref else ""
        )
    else:
        label = pi_config.package_json_field(root / "package.json", "name") if root else None
        name = label or Path(source.path or source.raw).name or source.raw
    extra["component_path"] = [{"type": "plugin", "name": name}]
    return ComponentRef(
        ecosystem=ecosystem,
        name=name,
        version=version,
        source_manifest=str(layer.settings_path),
        source_locator=f"$.packages[{entry.index}]",
        component_identity=identity,
        extra=extra,
    )


def _in_package(ref: ComponentRef, package: _Package | None) -> ComponentRef:
    if package is None:
        return ref
    component_type = str(ref.extra.get("component_type"))
    name = ref.name or ""
    return replace(
        ref,
        extra={
            **ref.extra,
            "declared_by": {"kind": "plugin", "name": package.name, "path": str(package.path)},
            "component_path": [
                {"type": "plugin", "name": package.name},
                {"type": component_type, "name": name},
            ],
        },
    )


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


# --- Installed -------------------------------------------------------------------


def build_pi_installed_graph(
    agent,
    *,
    home: Path,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Compose an installed Pi agent.

    `agent.config_root` is the agent directory, and `home` holds the user's
    `.agents/skills`. The project layer loads only as Pi's trust decision
    allows it.
    """
    agent_dir = Path(agent.config_root)
    project = Path(agent.project_root).resolve() if agent.project_root is not None else None
    top = _repository_root(project) if project is not None else None
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = make_normalizer(
        "endpoint",
        agent_dir,
        agent_dir,
        # From the repository root, so an ancestor's `.agents/skills` keys
        # under `project/` like the project's own files.
        top or project,
        agent.root_label,
        extra_roots=(("agents", home / ".agents"), ("home", home)),
    )
    user = _Layer(
        scope="user",
        base=agent_dir,
        settings_path=agent_dir / SETTINGS_FILENAME,
        settings=pi_config.load_settings(
            agent_dir / SETTINGS_FILENAME, record_gap=graph.record_gap
        ),
        agents_skills=(home / ".agents" / "skills",),
        owner=project,
    )
    layers = [user]
    mcp_files: list[tuple[Path, Literal["user", "project"]]] = [(agent_dir / MCP_FILENAME, "user")]
    if project is not None and _project_loads(graph, agent_dir, project, user.settings):
        pi_dir = project / PROJECT_CONFIG_DIR
        layers.insert(
            0,
            _Layer(
                scope="project",
                base=pi_dir,
                settings_path=pi_dir / SETTINGS_FILENAME,
                settings=pi_config.load_settings(
                    pi_dir / SETTINGS_FILENAME, record_gap=graph.record_gap
                ),
                agents_skills=_ancestor_agents_skills(project, top, home),
                owner=project,
            ),
        )
        mcp_files.append((pi_dir / MCP_FILENAME, "project"))
    composer = _Composer(
        graph,
        root,
        normalize,
        home=home,
        visible=lambda _path: True,
        stamp=True,
        install_roots={
            "user": agent_dir,
            "project": (project or agent_dir) / PROJECT_CONFIG_DIR,
        },
    )
    composer.add_mcp_servers(mcp_files)
    for layer in layers:
        composer.add_settings_resources(layer)
        composer.add_own_resources(layer)
    composer.add_packages(layers)
    return finalize_graph(
        graph,
        agent_dir,
        normalize,
        project_root=top or project,
        include_gitignored=include_gitignored,
        attach_include_gitignored=True,
        root_dir=None,
        root_spec=None,
        warnings=warnings,
    )


def _project_loads(graph: Graph, agent_dir: Path, project: Path, user_settings: dict) -> bool:
    """Pi's trust decision for `project`, read from disk (docs/specs/pi-agent-kind.md,
    Project trust). Only a recorded `false`, or no record and a `"never"`
    default, keeps the project from loading."""
    trust_path = agent_dir / TRUST_FILENAME
    try:
        decision = pi_config.trust_decision(pi_config.load_trust(trust_path), project)
    except (OSError, ValueError) as exc:
        graph.record_gap(f"could not parse {trust_path}: {exc}")
        decision = None
    if decision is None and user_settings.get("defaultProjectTrust") == "never":
        decision = False
    if decision is False:
        graph.warnings.append(
            f"{project} is not trusted, so Pi does not load its .pi configuration"
        )
        return False
    if decision is None:
        graph.warnings.append(
            f"{project} has no trust decision; its .pi configuration is reported as a "
            "trusted run loads it"
        )
    return True


def _repository_root(project: Path) -> Path | None:
    """The nearest directory at or above `project` holding `.git`."""
    for directory in (project, *project.parents):
        if (directory / ".git").exists():
            return directory
    return None


def _ancestor_agents_skills(project: Path, top: Path | None, home: Path) -> tuple[Path, ...]:
    """`.agents/skills` in the project and each ancestor, up to the repository
    root, else to the file system root. The user's own is not a project one."""
    user = _resolved(home / ".agents" / "skills")
    found: list[Path] = []
    for directory in (project, *project.parents):
        candidate = directory / ".agents" / "skills"
        if _resolved(candidate) != user:
            found.append(candidate)
        if top is not None and directory == top:
            break
    return tuple(found)


# --- Declared (repo) -----------------------------------------------------------


def build_pi_declared_graph(
    agent,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Compose a Pi agent a repository declares: each `.pi/` at any depth as
    one project, and each `.agents/skills`. Nothing outside the repository and
    nothing it ignores is read."""
    scan_root = Path(agent.scan_root)
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = make_normalizer("repo", scan_root, scan_root, None, agent.root_label)
    root_spec = None if include_gitignored else load_gitignore_spec(scan_root)
    resolved_root = _resolved(scan_root)

    def visible(path: Path) -> bool:
        resolved = _resolved(path)
        if not resolved.is_relative_to(resolved_root):
            return False
        return not is_ignored_under(path, scan_root, root_spec)

    projects, agents_skills = _declared_roots(scan_root, root_spec)
    composer = _Composer(
        graph,
        root,
        normalize,
        home=None,
        visible=visible,
        stamp=False,
        install_roots=None,
        root_dir=scan_root,
        root_spec=root_spec,
    )
    for project in projects:
        pi_dir = project / PROJECT_CONFIG_DIR
        settings_path = pi_dir / SETTINGS_FILENAME
        layer = _Layer(
            scope="project",
            base=pi_dir,
            settings_path=settings_path,
            settings=pi_config.load_settings(settings_path, record_gap=graph.record_gap)
            if visible(settings_path)
            else {},
        )
        composer.add_mcp_servers([(pi_dir / MCP_FILENAME, "project")])
        composer.add_settings_resources(layer)
        composer.add_own_resources(layer)
        composer.add_packages([layer])
    for skills_root in agents_skills:
        for skill_dir in composer.skill_dirs(skills_root):
            composer.add_skill(root, skill_dir)
    composer.add_deps(root, scan_root)
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


def _declared_roots(scan_root: Path, spec) -> tuple[list[Path], list[Path]]:
    """Every directory holding a `.pi/`, and every `.agents/skills`, that a
    file the walk keeps sits under."""
    projects: set[Path] = set()
    agents_skills: set[Path] = set()
    for path in iter_unignored_files(scan_root, spec):
        parts = path.relative_to(scan_root).parts
        for index, part in enumerate(parts[:-1]):
            if part == PROJECT_CONFIG_DIR:
                projects.add(scan_root.joinpath(*parts[:index]))
                break
        for index in range(len(parts) - 2):
            if parts[index] == ".agents" and parts[index + 1] == "skills":
                agents_skills.add(scan_root.joinpath(*parts[: index + 2]))
                break
    return sorted(projects), sorted(agents_skills)
