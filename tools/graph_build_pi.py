"""Compose Pi's package and resource selection without executing extension code."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tools.component_ref import ComponentRef
from tools.graph import Graph, Node
from tools.graph_build import add_child, make_normalizer, occurrence_key
from tools.parsers.gitignore import is_ignored
from tools.parsers.pi_manifest import (
    declaration_files,
    declared_ignore_spec,
    parse_package,
    read_settings,
    resource_project_root,
)
from tools.parsers.pi_settings import PiResource, expand_resources, permitted, resolve_resources
from tools.parsers.pi_source import parse_pi_source
from tools.parsers.pi_trust import project_trust, read_trust_store

_TYPES = {"extensions": "extension", "skills": "skill", "prompts": "command", "themes": "theme"}


def _settings(path: Path, graph: Graph, allowed_root: Path | None = None) -> dict:
    if not permitted(path, allowed_root) or not path.exists():
        return {}
    try:
        return read_settings(path)
    except (OSError, ValueError) as exc:
        graph.record_gap(f"Could not parse Pi settings at {path}: {type(exc).__name__}")
        return {}


def _shared_roots(project: Path) -> tuple[Path, ...]:
    roots = []
    for directory in (project, *project.parents):
        roots.append(directory / ".agents/skills")
        if (directory / ".git").exists():
            break
    return tuple(roots)


def build_pi_graph(agent, *, include_gitignored=False, warnings=None) -> Graph:
    root = Node(agent.bom_ref, "target", None)
    graph = Graph({root.key: root})
    declared = agent.source == "declared"
    boundary = Path(agent.scan_root) if declared else None
    target = boundary if declared else Path(agent.config_root)
    assert target is not None
    extra_roots: tuple[tuple[str, Path], ...] = (
        () if declared else (("agents", Path.home() / ".agents"),)
    )
    ancestor_roots: tuple[Path, ...] = ()
    if not declared and agent.project_root is not None:
        ancestor_roots = _shared_roots(agent.project_root)
        extra_roots += tuple(
            (f"project-ancestor-{distance}/agents", path.parent)
            for distance, path in enumerate(ancestor_roots[1:], start=1)
        )
    resolvable_roots = []
    for label, path in extra_roots:
        if permitted(path, None):
            resolvable_roots.append((label, path))
        else:
            graph.record_gap(f"Could not resolve Pi shared resource root at {path}")
    normalize = make_normalizer(
        "repo" if declared else "endpoint",
        target,
        target,
        None if declared else agent.project_root,
        "pi",
        tuple(resolvable_roots),
    )
    spec = declared_ignore_spec(target, include_gitignored=include_gitignored) if declared else None
    if declared:
        files = declaration_files(target, include_gitignored=include_gitignored)
        projects: set[Path] = set()
        package_rows: dict[Path, tuple[PiResource, ComponentRef]] = {}
        for path in files:
            project = resource_project_root(path, target)
            if project is not None:
                projects.add(project)
            elif path.name == "package.json":
                source = parse_pi_source(str(path.parent), base_dir=target)
                assert source is not None
                row = PiResource(
                    source,
                    str(path.parent),
                    "project",
                    None,
                    True,
                    declaration_path=path,
                    install_root=path.parent,
                )
                package_rows[path.parent] = (row, parse_package(path)[0])
        project_resources = {}
        selected_package_roots = set()
        for project in sorted(projects):
            settings_path = project / ".pi/settings.json"
            settings = _settings(settings_path, graph, target) if settings_path in files else {}
            rows = resolve_resources({}, settings, project_root=project, allowed_root=target)
            project_resources[project] = (settings, rows)
            selected_package_roots.update(
                row.install_root.resolve()
                for row in rows
                if row.resource_type == "packages"
                and row.install_root is not None
                and permitted(row.install_root, target)
            )
        package_rows = {
            path: row
            for path, row in package_rows.items()
            if path.resolve() not in selected_package_roots
        }
        for project, (settings, rows) in project_resources.items():
            package_refs = None
            package_row = package_rows.pop(project, None)
            if package_row is not None:
                row, ref = package_row
                rows = [row, *rows]
                package_refs = {id(row): ref}
            _compose_resources(
                graph,
                rows,
                normalize,
                boundary=target,
                spec=spec,
                project_root=project,
                project_settings=settings,
                shared_skill_roots=(project / ".agents/skills",),
                package_refs=package_refs,
            )
        for row, ref in package_rows.values():
            _compose_resources(
                graph,
                [row],
                normalize,
                boundary=target,
                spec=spec,
                package_refs={id(row): ref},
            )
    else:
        settings = _settings(target / "settings.json", graph)
        project = agent.project_root
        project_settings = {}
        shared: tuple = ((Path.home() / ".agents/skills", "global"),)
        if project is not None:
            trust = read_trust_store(target / "trust.json")
            verdict = (
                project_trust(trust.data, settings.get("defaultProjectTrust"), project)
                if trust.data is not None
                else "unresolved"
            )
            if verdict != "trusted":
                graph.record_gap(
                    f"Pi project resources excluded: persisted project trust is {verdict} "
                    f"at {project}"
                )
                project = None
            else:
                project_settings = _settings(project / ".pi/settings.json", graph)
                shared += ancestor_roots
        rows = resolve_resources(
            settings, project_settings, agent_root=target, project_root=project
        )
        _compose_resources(
            graph,
            rows,
            normalize,
            agent_root=target,
            project_root=project,
            global_settings=settings,
            project_settings=project_settings,
            shared_skill_roots=shared,
        )
    graph.warnings.append(
        "Pi composition is static: invocation overrides, extension registration, "
        "and dynamic project trust outcomes are not observed"
    )
    graph.validate()
    if warnings is not None:
        warnings.extend(graph.warnings)
        for gap in graph.warnings.gaps:
            if hasattr(warnings, "gaps") and gap not in warnings.gaps:
                warnings.gaps.append(gap)
    return graph


def _base(row: PiResource) -> PiResource:
    while row.delta_base is not None:
        row = row.delta_base
    return row


def _package_ref(row: PiResource, boundary: Path | None) -> ComponentRef:
    source = row.source
    installed = (
        row.install_root is not None
        and permitted(row.install_root, boundary)
        and row.install_root.exists()
    )
    name = source.identity.split(":", 1)[1]
    provenance = {
        "status": "known" if source.kind != "local" else "unknown",
        "source_type": source.kind,
        "source": name,
    }
    if source.ref:
        provenance["ref"] = source.ref
    return ComponentRef(
        ecosystem="npm" if source.kind == "npm" else None,
        name=name if source.kind == "npm" else Path(name).name,
        version=row.installed_version,
        source_manifest=str(row.declaration_path or row.install_root or ""),
        source_locator=f"$.packages[{row.index}]",
        component_identity=f"plugin/git/{name}" if source.kind == "git" else None,
        extra={
            "component_type": "plugin",
            "agent_kind": "pi",
            "install_source": row.raw,
            "installed": installed,
            "source_provenance": provenance,
        },
    )


def _resource_ref(file, graph: Graph, normalize) -> ComponentRef:
    kind = _TYPES[file.resource_type]
    name = file.path.stem
    valid = True
    if kind == "skill":
        name = file.path.parent.name
        try:
            text = (
                file.path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            )
            end = text.find("\n---", 3) if text.startswith("---") else -1
            frontmatter = yaml.safe_load(text[4:end]) if end != -1 else {}
            if not isinstance(frontmatter, dict):
                frontmatter = {}
            if isinstance(frontmatter.get("name"), str) and frontmatter["name"]:
                name = frontmatter["name"]
            valid = isinstance(frontmatter.get("description"), str) and bool(
                frontmatter["description"].strip()
            )
        except (OSError, ValueError, yaml.YAMLError):
            valid = False
    elif kind == "theme":
        try:
            data = json.loads(file.path.read_text())
            name = data.get("name", "unnamed") if isinstance(data, dict) else "unnamed"
            valid = isinstance(name, str)
            if not valid:
                name = file.path.stem
        except (OSError, ValueError):
            valid = False
    if not valid:
        graph.record_gap(f"Invalid or unreadable Pi {kind} resource at {file.path}")
    provenance = {
        "status": "known",
        "source_type": "local",
        "source": normalize(str(file.path)),
        "origin": file.origin,
        "scope": file.scope,
    }
    if file.owner and file.owner.declaration_path:
        provenance["declaration"] = normalize(str(file.owner.declaration_path))
        provenance["index"] = file.owner.index
        provenance["configured_source"] = file.owner.raw
    return ComponentRef(
        name=name,
        source_manifest=str(file.path),
        source_locator="$.frontmatter" if kind == "skill" else "$",
        extra={
            "component_type": kind,
            "agent_kind": "pi",
            "enabled": file.enabled and valid,
            "source_provenance": provenance,
        },
    )


def _compose_resources(
    graph, rows, normalize, *, boundary=None, spec=None, package_refs=None, **kwargs
):
    files = expand_resources(rows, allowed_root=boundary, **kwargs)
    containers = {}
    for row in rows:
        base = _base(row)
        if row.resource_type == "packages":
            ref = (package_refs or {}).get(id(base)) or _package_ref(base, boundary)
            node = add_child(graph, graph.root, Node(occurrence_key(ref, normalize), "plugin", ref))
            containers[id(base)] = node
        for source in (row, base):
            for gap in source.gaps:
                graph.record_gap(f"Pi resource declaration at {source.declaration_path}: {gap}")
    names = set()
    for file in files:
        if boundary is not None:
            try:
                relative = file.path.relative_to(boundary)
            except ValueError:
                # expand_resources() only admits paths that are contained after
                # resolving symlinks (see permitted()); a lexically outside path
                # that is only in-bound via a symlink still needs a relative
                # path to check against .gitignore.
                relative = file.path.resolve().relative_to(boundary.resolve())
            if is_ignored(relative, spec):
                continue
        ref = _resource_ref(file, graph, normalize)
        kind = _TYPES[file.resource_type]
        if kind != "extension" and ref.extra["enabled"]:
            key = (kind, ref.name)
            if key in names:
                continue
            names.add(key)
        parent = containers.get(id(_base(file.owner))) if file.owner else None
        key = occurrence_key(ref, normalize)
        if key not in graph.nodes:
            add_child(graph, parent or graph.root, Node(key, kind, ref))
