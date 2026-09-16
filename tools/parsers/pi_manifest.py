"""Pi manifest entry points and bounded declaration discovery."""

from __future__ import annotations

import json
from pathlib import Path

from pathspec import GitIgnoreSpec

from tools.component_ref import ComponentRef
from tools.parsers.gitignore import is_ignored, iter_unignored_files, load_gitignore_spec
from tools.parsers.pi_settings import RESOURCE_TYPES, permitted, read_manifest


def read_settings(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Pi settings must be an object")
    for key in ("packages", *RESOURCE_TYPES):
        if key in value and not isinstance(value[key], list):
            raise ValueError(f"Pi {key} must be an array")
        for entry in value.get(key, []):
            if isinstance(entry, str):
                continue
            if (
                key != "packages"
                or not isinstance(entry, dict)
                or not isinstance(entry.get("source"), str)
            ):
                raise ValueError(f"Invalid Pi {key} entry")
            for resource in RESOURCE_TYPES:
                if resource in entry and (
                    not isinstance(entry[resource], list)
                    or not all(isinstance(item, str) for item in entry[resource])
                ):
                    raise ValueError("Invalid Pi package resource filter")
    return value


def parse_settings(path: Path) -> list[ComponentRef]:
    read_settings(path)
    return []


def is_package(data: dict) -> bool:
    return isinstance(data.get("pi"), dict) or (
        isinstance(data.get("keywords"), list) and "pi-package" in data["keywords"]
    )


def parse_package(path: Path) -> list[ComponentRef]:
    data = read_manifest(path)
    if not is_package(data):
        return []
    name = data.get("name")
    version = data.get("version")
    return [
        ComponentRef(
            ecosystem="npm" if isinstance(name, str) and name else None,
            name=name if isinstance(name, str) and name else path.parent.name,
            version=version if isinstance(version, str) else None,
            source_manifest=str(path),
            source_locator="$.pi",
            extra={"component_type": "plugin"},
        )
    ]


def declaration_guard(path: Path, root: Path | None = None, spec=None) -> bool:
    root = root or path.parent
    if not permitted(path, root):
        return False
    parts = path.relative_to(root).parts
    if "node_modules" in parts or any(
        parts[i : i + 2] in ((".pi", "npm"), (".pi", "git")) for i in range(len(parts) - 1)
    ):
        return False
    marker = next((i for i, part in enumerate(parts) if part in (".pi", ".agents")), None)
    own_project_root = root.joinpath(*parts[:marker]) if marker is not None else None
    for parent in path.parents:
        if parent == root.parent:
            break
        if parent == own_project_root:
            continue
        manifest = parent / "package.json"
        if (
            manifest != path
            and permitted(manifest, root)
            and not is_ignored(manifest.relative_to(root), spec)
            and is_package(read_manifest(manifest, root))
        ):
            return False
    return path.name != "package.json" or is_package(read_manifest(path, root))


def declared_ignore_spec(root: Path, *, include_gitignored: bool = False):
    if include_gitignored or not permitted(root / ".gitignore", root):
        return None
    return load_gitignore_spec(root)


def declaration_files(root: Path, *, include_gitignored: bool = False) -> list[Path]:
    base = declared_ignore_spec(root, include_gitignored=include_gitignored)
    patterns = [p.pattern for p in base.patterns if isinstance(p.pattern, str)] if base else []
    spec = GitIgnoreSpec.from_lines([*patterns, "node_modules/", "**/.pi/npm/", "**/.pi/git/"])
    result = []
    suffixes = {
        "extensions": (".ts", ".js"),
        "skills": (".md",),
        "prompts": (".md",),
        "themes": (".json",),
    }
    for path in iter_unignored_files(root, spec):
        parts = path.relative_to(root).parts
        native = any(
            parts[i] == ".pi"
            and (parts[i + 1] == "settings.json" or path.suffix in suffixes.get(parts[i + 1], ()))
            for i in range(len(parts) - 1)
        )
        shared = path.suffix == ".md" and any(
            parts[i : i + 2] == (".agents", "skills") and len(parts) > i + 3
            for i in range(len(parts) - 1)
        )
        if (path.name == "package.json" or native or shared) and declaration_guard(
            path, root, spec
        ):
            result.append(path)
    return result
