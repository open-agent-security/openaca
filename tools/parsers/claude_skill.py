"""Parse SKILL.md frontmatter per the agentskills.io spec.

The canonical spec (agentskills.io/specification) defines six top-level
frontmatter fields: `name` (required, expected to match parent dir),
`description` (required), `license`, `compatibility`, `metadata`, and
`allowed-tools`. There is no top-level `version` field — versioning is
convention via `metadata.version` (rejected here unless it's a string,
because YAML coerces `1.0` to a float and `1.0.0` to a string, which is
the kind of foot-gun we want to refuse silently rather than guess).

Identity: `skill/<name>` or `skill/<name>@<metadata.version>`.

Used for both direct skills (`~/.claude/skills/<name>/SKILL.md`, no parent
plugin) and bundled skills (`<plugin>/skills/<name>/SKILL.md`, parented
to a plugin). Bundled skills carry `attributed_to`; direct skills don't —
the caller decides which by passing or omitting the kwarg.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Optional

import yaml

from tools.component_ref import ComponentRef


def parse(skill_md_path: Path, attributed_to: Optional[str] = None) -> list[ComponentRef]:
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    frontmatter = _extract_frontmatter(text)
    if frontmatter is None:
        return []
    raw_name = frontmatter.get("name")
    if isinstance(raw_name, str) and raw_name:
        name = raw_name
    else:
        # Spec says name must match parent dir; if frontmatter omits it
        # (or supplies a non-string / empty), fall back to the dir name
        # so inventory scans still see the skill.
        name = skill_md_path.parent.name
    if not name:
        return []
    version = _extract_version(frontmatter)
    identity = f"skill/{name}"
    if version:
        identity = f"{identity}@{version}"
    extra: dict[str, object] = {"component_type": "skill"}
    coordinate = _skill_tree_coordinate(skill_md_path.parent)
    if coordinate is not None:
        extra["artifact_coordinates"] = [coordinate]
    return [
        ComponentRef(
            name=name,
            version=version,
            component_identity=identity,
            source_manifest=str(skill_md_path),
            source_locator="$.frontmatter",
            attributed_to=attributed_to,
            extra=extra,
        )
    ]


def _extract_frontmatter(text: str) -> Optional[dict]:
    """Return the YAML mapping at the top of `text`, or None on any failure.

    Frontmatter is a `---` block at the very start of the file. Anything
    else — no opening marker, no closing marker, non-mapping content,
    malformed YAML — returns None so the caller skips the file.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end].strip()
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _extract_version(frontmatter: dict) -> Optional[str]:
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return None
    version = metadata.get("version")
    if isinstance(version, str) and version:
        return version
    return None


def _skill_tree_coordinate(skill_dir: Path) -> dict[str, str] | None:
    digest = _skill_tree_hash(skill_dir)
    if digest is None:
        return None
    return {
        "kind": "skill-tree-hash",
        "algorithm": "sha256",
        "value": f"sha256:{digest}",
    }


def _skill_tree_hash(skill_dir: Path) -> str | None:
    tree_entries: list[tuple[str, str, str]] = []
    try:
        paths = list(skill_dir.rglob("*"))
    except (OSError, RuntimeError):
        return None

    for path in paths:
        rel_path = _normalize_skill_path(path.relative_to(skill_dir).as_posix())
        if not rel_path or rel_path == "skill.sig":
            continue
        try:
            if path.is_symlink():
                entry_type = "file"
                content = _symlink_content(path, skill_dir)
            elif path.is_dir():
                entry_type = "dir"
                content = b""
            elif path.is_file():
                entry_type = "file"
                content = path.read_bytes()
            else:
                continue
        except (OSError, RuntimeError):
            return None
        content_hash = hashlib.sha256(content).hexdigest()
        tree_entries.append((entry_type, rel_path, content_hash))

    tree_entries.sort(key=lambda item: item[1].encode("utf-8"))
    hasher = hashlib.sha256()
    for entry_type, path, content_hash in tree_entries:
        hasher.update(f"{entry_type}\0{path}\0{content_hash}\n".encode("utf-8"))
    return hasher.hexdigest()


def _normalize_skill_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    normalized = unicodedata.normalize("NFC", normalized)
    normalized = str(PurePosixPath(normalized))
    while normalized.startswith("./") or normalized.startswith("/"):
        normalized = normalized[2:] if normalized.startswith("./") else normalized[1:]
    return "" if normalized == "." else normalized


def _symlink_content(path: Path, skill_dir: Path) -> bytes:
    target = path.resolve(strict=False)
    root = skill_dir.resolve(strict=False)
    if target.is_relative_to(root) and target.is_file():
        return target.read_bytes()
    return os.readlink(path).encode("utf-8")
