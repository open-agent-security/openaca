"""Enumerate slash commands and subagents (`commands/*.md`, `agents/*.md`).

Both surfaces use the same shape — a directory of markdown files where
the filename basename is the canonical name and optional YAML
frontmatter may override it via a `name:` field.

Identity:

- Plugin-bundled commands: `claude-command/<owner>/<name>`
- Plugin-bundled agents:   `claude-agent/<owner>/<name>`
- Repo-declared commands:  `claude-command/<name>`
- Repo-declared agents:    `claude-agent/<name>`

For plugin-bundled components the plugin name is part of logical identity
because the same command name can appear in multiple plugins (ADR-0013).
For repo-declared components there is no logical owner; `scope_owner=None`
signals this. Observation metadata (the repo context) is carried in `extra`.

V0 has no version field for commands or agents; matcher fires on
identity-only (name-only) matching. Sufficient for inventory; T3
content-hash advisories would refine if needed in V1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml

from tools.component_ref import ComponentRef

Kind = Literal["command", "agent"]


def parse_file(
    md_path: Path,
    kind: Kind,
    scope_owner: Optional[str] = None,
    attributed_to: Optional[str] = None,
) -> list[ComponentRef]:
    """Emit one ref for a single `*.md` file. Used by the repo-mode
    registry where `rglob` discovers paths individually."""
    if not md_path.is_file() or md_path.suffix != ".md":
        return []
    name = _resolve_name(md_path)
    ecosystem = f"claude-{kind}"
    identity = (
        f"{ecosystem}/{scope_owner}/{name}" if scope_owner is not None else f"{ecosystem}/{name}"
    )
    return [
        ComponentRef(
            ecosystem=ecosystem,
            name=name,
            component_identity=identity,
            source_manifest=str(md_path),
            source_locator="$",
            attributed_to=attributed_to,
            extra={"scope_owner": scope_owner},
        )
    ]


def enumerate_dir(
    dir_path: Path,
    kind: Kind,
    scope_owner: Optional[str],
    attributed_to: Optional[str],
) -> list[ComponentRef]:
    """Walk `dir_path/*.md`, emit one ComponentRef per file.

    `scope_owner` is the plugin name for bundled components, or None for
    repo-declared ones. It is retained in `extra` as observation metadata.
    `attributed_to` is the parent plugin's identity for bundled components,
    or None for repo-declared.
    """
    if not dir_path.is_dir():
        return []
    ecosystem = f"claude-{kind}"
    refs: list[ComponentRef] = []
    # Sort for deterministic emission order — makes diffs in fixture
    # snapshots and verbose output stable across runs.
    for child in sorted(dir_path.iterdir()):
        if not child.is_file() or child.suffix != ".md":
            continue
        name = _resolve_name(child)
        identity = (
            f"{ecosystem}/{scope_owner}/{name}"
            if scope_owner is not None
            else f"{ecosystem}/{name}"
        )
        refs.append(
            ComponentRef(
                ecosystem=ecosystem,
                name=name,
                component_identity=identity,
                source_manifest=str(child),
                source_locator="$",
                attributed_to=attributed_to,
                extra={"scope_owner": scope_owner},
            )
        )
    return refs


def _resolve_name(md_path: Path) -> str:
    """Frontmatter `name:` wins; otherwise the filename without `.md`."""
    fallback = md_path.stem
    try:
        text = md_path.read_text()
    except (OSError, UnicodeDecodeError):
        return fallback
    if not text.startswith("---"):
        return fallback
    end = text.find("\n---", 3)
    if end == -1:
        return fallback
    block = text[3:end].strip()
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return fallback
    if not isinstance(data, dict):
        return fallback
    declared = data.get("name")
    if isinstance(declared, str) and declared:
        return declared
    return fallback
