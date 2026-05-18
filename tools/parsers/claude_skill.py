"""Parse SKILL.md frontmatter per the agentskills.io spec.

The canonical spec (agentskills.io/specification) defines six top-level
frontmatter fields: `name` (required, expected to match parent dir),
`description` (required), `license`, `compatibility`, `metadata`, and
`allowed-tools`. There is no top-level `version` field — versioning is
convention via `metadata.version` (rejected here unless it's a string,
because YAML coerces `1.0` to a float and `1.0.0` to a string, which is
the kind of foot-gun we want to refuse silently rather than guess).

Identity: `claude-skill/<name>` or `claude-skill/<name>@<metadata.version>`.

Used for both direct skills (`~/.claude/skills/<name>/SKILL.md`, no parent
plugin) and bundled skills (`<plugin>/skills/<name>/SKILL.md`, parented
to a plugin). Bundled skills carry `attributed_to`; direct skills don't —
the caller decides which by passing or omitting the kwarg.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from tools.component_ref import ComponentRef


def parse(skill_md_path: Path, attributed_to: Optional[str] = None) -> list[ComponentRef]:
    try:
        text = skill_md_path.read_text()
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
    identity = f"claude-skill/{name}"
    if version:
        identity = f"{identity}@{version}"
    return [
        ComponentRef(
            ecosystem="claude-skill",
            name=name,
            version=version,
            component_identity=identity,
            source_manifest=str(skill_md_path),
            source_locator="$.frontmatter",
            attributed_to=attributed_to,
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
