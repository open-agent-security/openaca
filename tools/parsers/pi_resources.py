"""Filesystem expansion of Pi resources; never installs or executes packages."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pathspec import GitIgnoreSpec

if TYPE_CHECKING:
    from tools.parsers.pi_settings import PiResource, Scope


@dataclass(frozen=True)
class PiResourceFile:
    path: Path
    resource_type: str
    enabled: bool
    scope: Scope
    owner: PiResource | None
    origin: str = "package"


def _glob_expression(pattern: str) -> str:
    return (
        re.escape(pattern)
        .replace(r"\*\*/", "DOUBLESTARSLASH")
        .replace(r"\*\*", "DOUBLESTAR")
        .replace(r"\*", "[^/]*")
        .replace(r"\?", "[^/]")
        .replace("DOUBLESTARSLASH", "(?:.*/)?")
        .replace("DOUBLESTAR", ".*")
    )


def _match(path: Path, pattern: str, base: Path, exact: bool = False) -> bool:

    paths = [path]
    if path.name == "SKILL.md":
        paths.append(path.parent)
    for candidate in paths:
        values = [os.path.relpath(candidate, base), str(candidate)]
        if not exact:
            values.append(candidate.name)
        normalized = pattern.removeprefix("./") if exact else pattern
        if exact and normalized in values:
            return True
        if not exact:
            if any(re.fullmatch(_glob_expression(normalized), value) for value in values):
                return True
    return False


def _state(path: Path, patterns: list[str], base: Path, delta: bool = False) -> bool | None:
    if delta:
        state = None
        for p in patterns:
            prefixed = p.startswith(("!", "+", "-"))
            if _match(path, p[1:] if prefixed else p, base, p.startswith(("+", "-"))):
                state = not p.startswith(("!", "-"))
        return state
    includes = [p for p in patterns if not p.startswith(("!", "+", "-"))]
    state = not includes or any(_match(path, p, base) for p in includes)
    for prefix, value in [("!", False), ("+", True), ("-", False)]:
        if any(_match(path, p[1:], base, prefix != "!") for p in patterns if p.startswith(prefix)):
            state = value
    return state


def _collect(
    root: Path, kind: str, boundary: Path | None, *, auto: bool = False, agents: bool = False
) -> list[Path]:
    from tools.parsers.pi_settings import permitted, read_manifest

    seen: set[Path] = set()

    def ignored(
        path: Path, rules: list[tuple[Path, GitIgnoreSpec]], directory: bool = False
    ) -> bool:
        state = False
        for base, spec in rules:
            result = spec.check_file(path.relative_to(base).as_posix() + ("/" if directory else ""))
            if result.include is not None:
                state = result.include
        return state

    def visit(path: Path, depth: int, rules: list[tuple[Path, GitIgnoreSpec]]) -> list[Path]:
        if not permitted(path, boundary) or not path.exists():
            return []
        if path.is_file():
            return [path]
        real = path.resolve()
        if real in seen:
            return []
        seen.add(real)
        lines: list[str] = []
        for name in (".gitignore", ".ignore", ".fdignore"):
            ignore = path / name
            if permitted(ignore, boundary):
                try:
                    lines.extend(ignore.read_text().splitlines())
                except OSError:
                    pass
        rules = [*rules, (path, GitIgnoreSpec.from_lines(lines))]
        if kind == "skills" and (path / "SKILL.md").is_file():
            skill = path / "SKILL.md"
            if permitted(skill, boundary) and not ignored(skill, rules):
                return [skill]
        if kind == "extensions":
            manifest = read_manifest(path / "package.json", boundary).get("pi", {})
            entries = manifest.get("extensions", []) if isinstance(manifest, dict) else []
            selected = [
                path / p
                for p in entries
                if isinstance(p, str) and permitted(path / p, boundary) and (path / p).exists()
            ]
            if selected:
                return selected
            for name in ("index.ts", "index.js"):
                if permitted(path / name, boundary) and (path / name).is_file():
                    return [path / name]
            if depth > 0:
                return []
        result = []
        try:
            children = sorted(path.iterdir())
        except OSError:
            return []
        for child in children:
            if (
                child.name.startswith(".")
                or child.name == "node_modules"
                or not permitted(child, boundary)
            ):
                continue
            directory = child.is_dir()
            if ignored(child, rules, directory):
                continue
            if directory:
                if kind == "extensions" and depth >= 1:
                    continue
                if auto and kind in ("prompts", "themes"):
                    continue
                result.extend(visit(child, depth + 1, rules))
            elif kind == "extensions" and child.suffix in (".ts", ".js"):
                result.append(child)
            elif (
                kind == "skills"
                and child.suffix == ".md"
                and ((not agents and depth == 0) or (agents and depth > 0))
            ):
                result.append(child)
            elif kind == "prompts" and child.suffix == ".md":
                result.append(child)
            elif kind == "themes" and child.suffix == ".json":
                result.append(child)
        return result

    return visit(root, 0, [])


def _glob_paths(root: Path, pattern: str, boundary: Path | None) -> list[Path]:
    from tools.parsers.pi_settings import permitted

    found = []
    seen: set[Path] = set()
    absolute = Path(pattern).is_absolute()
    search_root = root
    if absolute:
        prefix = re.split(r"[*?]", pattern, maxsplit=1)[0]
        search_root = Path(prefix) if prefix.endswith(os.sep) else Path(prefix).parent

    def walk(path: Path):
        if not permitted(path, boundary) or path.resolve() in seen:
            return
        seen.add(path.resolve())
        try:
            children = sorted(path.iterdir())
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or not permitted(child, boundary):
                continue
            candidate = child.as_posix() if absolute else child.relative_to(root).as_posix()
            if re.fullmatch(_glob_expression(pattern), candidate):
                found.append(child)
            if child.is_dir():
                walk(child)

    walk(search_root)
    return found


def expand_resources(
    resources: list[PiResource],
    *,
    agent_root: Path | None = None,
    project_root: Path | None = None,
    global_settings: dict | None = None,
    project_settings: dict | None = None,
    shared_skill_roots: tuple[Path | tuple[Path, Scope], ...] = (),
    allowed_root: Path | None = None,
) -> list[PiResourceFile]:
    from tools.parsers.pi_settings import RESOURCE_TYPES, permitted, read_manifest

    found: dict[tuple[str, Path], PiResourceFile] = {}

    def add(
        path: Path,
        kind: str,
        state: bool | None,
        scope: Scope,
        owner: PiResource | None,
        origin: str,
    ):
        if state is not None and permitted(path, allowed_root):
            found.setdefault(
                (kind, Path(os.path.abspath(path))),
                PiResourceFile(path, kind, state, scope, owner, origin),
            )

    def package(row: PiResource):
        root = row.install_root
        if root is None or not permitted(root, allowed_root) or not root.exists():
            return
        if root.is_file():
            add(root, "extensions", True if row.autoload else None, row.scope, row, "package")
            return
        manifest = read_manifest(root / "package.json", allowed_root).get("pi")
        if (
            row.source.kind == "local"
            and row.filters is None
            and not isinstance(manifest, dict)
            and not any(
                permitted(root / kind, allowed_root) and (root / kind).exists()
                for kind in RESOURCE_TYPES
            )
        ):
            add(root, "extensions", True, row.scope, row, "package")
            return
        all_patterns = [p for values in (row.filters or {}).values() for p in values]
        if isinstance(manifest, dict):
            all_patterns.extend(
                p
                for values in manifest.values()
                if isinstance(values, list)
                for p in values
                if isinstance(p, str)
            )
        if any(any(char in p for char in "[]{}()") for p in all_patterns):
            row.gaps = tuple(dict.fromkeys((*row.gaps, "unsupported advanced glob pattern")))
        for kind in RESOURCE_TYPES:
            entries = manifest.get(kind) if isinstance(manifest, dict) else None
            filtered = row.filters is not None and kind in row.filters
            if isinstance(entries, list) and (entries or not filtered):
                files = []
                for entry in entries:
                    if not isinstance(entry, str) or entry.startswith(("!", "+", "-")):
                        continue
                    paths = [root / entry]
                    if "*" in entry or "?" in entry:
                        # The scan boundary is checked before enumerating glob roots.
                        anchor = re.split(r"[*?]", entry)[0]
                        if not permitted(root / anchor, allowed_root):
                            row.gaps = tuple(
                                dict.fromkeys((*row.gaps, "resource outside allowed root"))
                            )
                            continue
                        paths = _glob_paths(root, entry, allowed_root)
                    for path in paths:
                        if not permitted(path, allowed_root):
                            row.gaps = tuple(
                                dict.fromkeys((*row.gaps, "resource outside allowed root"))
                            )
                            continue
                        if not path.exists():
                            row.gaps = tuple(
                                dict.fromkeys((*row.gaps, "manifest resource missing"))
                            )
                            continue
                        if any(
                            p.startswith(".") and p not in (".", "..")
                            for p in Path(os.path.relpath(path, root)).parts
                        ) and ("*" in entry or "?" in entry):
                            continue
                        files.extend(_collect(path, kind, allowed_root))
                overrides = [
                    p for p in entries if isinstance(p, str) and p.startswith(("!", "+", "-"))
                ]
                files = [f for f in files if _state(f, overrides, root)]
            elif isinstance(manifest, dict) and row.filters is None:
                files = []
            else:
                files = _collect(root / kind, kind, allowed_root)
            patterns = (row.filters or {}).get(kind, [])
            for path in files:
                state = (
                    _state(path, patterns, root, True)
                    if not row.autoload
                    else (False if filtered and not patterns else _state(path, patterns, root))
                )
                add(path, kind, state, row.scope, row, "package")
        if row.delta_base:
            package(row.delta_base)

    for row in resources:
        if row.resource_type == "packages":
            package(row)
    for row in resources:
        if row.resource_type != "packages" and row.install_root:
            settings = project_settings if row.scope == "project" else global_settings
            entries = (settings or {}).get(row.resource_type, [])
            patterns = [
                p
                for p in entries
                if isinstance(p, str) and (p.startswith(("!", "+", "-")) or "*" in p or "?" in p)
            ]
            base = row.declaration_path.parent if row.declaration_path else row.install_root.parent
            for path in _collect(row.install_root, row.resource_type, allowed_root):
                add(path, row.resource_type, _state(path, patterns, base), row.scope, row, "local")
    for scope, base, settings in [
        ("project", project_root / ".pi" if project_root else None, project_settings),
        ("global", agent_root, global_settings),
    ]:
        if base is None:
            continue
        for kind in RESOURCE_TYPES:
            overrides = [
                p
                for p in (settings or {}).get(kind, [])
                if isinstance(p, str) and p.startswith(("!", "+", "-"))
            ]
            for path in _collect(base / kind, kind, allowed_root, auto=True):
                add(path, kind, _state(path, overrides, base), cast("Scope", scope), None, "auto")
    for shared in shared_skill_roots:
        root, shared_scope = shared if isinstance(shared, tuple) else (shared, "project")
        settings = project_settings if shared_scope == "project" else global_settings
        overrides = [
            p
            for p in (settings or {}).get("skills", [])
            if isinstance(p, str) and p.startswith(("!", "+", "-"))
        ]
        for path in _collect(root, "skills", allowed_root, agents=True):
            add(
                path,
                "skills",
                _state(path, overrides, root.parent),
                cast("Scope", shared_scope),
                None,
                "auto",
            )

    def priority(file: PiResourceFile) -> int:
        if file.origin == "package":
            return 4
        return (0 if file.scope == "project" else 2) + (file.origin == "auto")

    canonical: dict[tuple[str, Path], PiResourceFile] = {}
    for file in sorted(found.values(), key=priority):
        canonical.setdefault((file.resource_type, file.path.resolve()), file)
    return list(canonical.values())
