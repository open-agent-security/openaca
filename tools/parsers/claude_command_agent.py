"""Enumerate slash commands and subagents (`commands/*.md`, `agents/*.md`).

Both surfaces use the same shape — a directory of markdown files where
the filename basename is the canonical name and optional YAML
frontmatter may override it via a `name:` field.

Identity scopes:

- Plugin-bundled commands: `claude-command/<plugin>/<name>`
- Plugin-bundled agents:   `claude-agent/<plugin>/<name>`
- Repo-declared:           `claude-command/repo/<name>`, `claude-agent/repo/<name>`

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


def enumerate_dir(
    dir_path: Path,
    kind: Kind,
    scope_owner: str,
    attributed_to: Optional[str],
) -> list[ComponentRef]:
    """Walk `dir_path/*.md`, emit one ComponentRef per file.

    `scope_owner` is the plugin name for bundled components, or the
    literal "repo" for repo-declared ones. `attributed_to` is the parent
    plugin's identity for bundled components, or None for repo-declared.
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
        identity = f"{ecosystem}/{scope_owner}/{name}"
        refs.append(
            ComponentRef(
                ecosystem=ecosystem,
                name=name,
                component_identity=identity,
                source_manifest=str(child),
                source_locator="$",
                attributed_to=attributed_to,
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
