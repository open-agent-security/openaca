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
from tools.parsers import hooks_json, mcp_json

Kind = Literal["command", "agent"]


def parse_file(
    md_path: Path,
    kind: Kind,
    scope_owner: Optional[str] = None,
    *,
    strict: bool = False,
    extensions: tuple[str, ...] = (".md",),
) -> list[ComponentRef]:
    """Emit one ref for a single file. Used by the repo-mode registry where
    `rglob` discovers paths individually.

    `extensions` defaults to Claude Code's `.md`-only surface. Cursor's two
    surfaces accept disjoint, differently-cased sets (commands: `.md`/`.txt`;
    subagents: `.md`/`.mdc`/`.markdown` — see `tools/cursor_commands.py` and
    `tools/cursor_subagents.py`), so callers on that surface pass their own
    set rather than this function guessing from `kind`.
    """
    if not md_path.is_file() or md_path.suffix not in extensions:
        return []
    frontmatter = _read_frontmatter(md_path, strict=strict)
    name = _resolve_name(md_path, frontmatter)
    ecosystem = f"claude-{kind}"
    identity = (
        f"{ecosystem}/{scope_owner}/{name}" if scope_owner is not None else f"{ecosystem}/{name}"
    )
    parent = ComponentRef(
        name=name,
        component_identity=identity,
        source_manifest=str(md_path),
        source_locator="$",
        extra={"scope_owner": scope_owner, "component_type": kind},
    )
    refs = [parent]
    if kind == "agent":
        refs.extend(_agent_frontmatter_child_refs(md_path, frontmatter, strict=strict))
    return refs


def enumerate_dir(
    dir_path: Path,
    kind: Kind,
    scope_owner: Optional[str],
) -> list[ComponentRef]:
    """Walk `dir_path/*.md`, emit one ComponentRef per file.

    `scope_owner` is the plugin name for bundled components, or None for
    repo-declared ones. It is retained in `extra` as observation metadata.
    Parentage is set by the graph edge, not stored on the refs.
    """
    if not dir_path.is_dir():
        return []
    refs: list[ComponentRef] = []
    # Sort for deterministic emission order — makes diffs in fixture
    # snapshots and verbose output stable across runs.
    for child in sorted(dir_path.rglob("*.md")):
        if not child.is_file() or child.suffix != ".md":
            continue
        refs.extend(parse_file(child, kind=kind, scope_owner=scope_owner))
    return refs


def _resolve_name(md_path: Path, frontmatter: Optional[dict] = None) -> str:
    """Frontmatter `name:` wins; otherwise the filename without `.md`."""
    fallback = md_path.stem
    if frontmatter is None:
        frontmatter = _read_frontmatter(md_path)
    declared = frontmatter.get("name") if isinstance(frontmatter, dict) else None
    if isinstance(declared, str) and declared:
        return declared
    return fallback


def _read_frontmatter(md_path: Path, *, strict: bool = False) -> dict:
    try:
        text = md_path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        if strict:
            raise ValueError("could not read frontmatter") from exc
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        if strict:
            raise ValueError("frontmatter is not terminated")
        return {}
    block = text[3:end].strip()
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        if strict:
            raise ValueError("frontmatter contains invalid YAML") from exc
        return {}
    if not isinstance(data, dict):
        if strict:
            raise ValueError("frontmatter must contain an object")
        return {}
    return data


def _agent_frontmatter_child_refs(
    md_path: Path,
    frontmatter: dict,
    *,
    strict: bool = False,
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []

    mcp_servers = _inline_mcp_servers(frontmatter.get("mcpServers"), strict=strict)
    refs.extend(
        mcp_json.parse_mcp_servers(
            mcp_servers,
            source_manifest=str(md_path),
            locator_prefix="$.mcpServers",
            strict=strict,
        )
    )

    hooks_block = frontmatter.get("hooks")
    if hooks_block is not None:
        refs.extend(
            hooks_json.parse_plugin_hooks_inline(
                hooks_block=hooks_block,
                plugin_name="",
                source_manifest=str(md_path),
                strict=strict,
            )
        )

    return refs


def _inline_mcp_servers(value: object, *, strict: bool = False) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if not isinstance(value, list):
        if strict:
            raise ValueError("agent mcpServers must be an object or array")
        return {}
    servers: dict = {}
    for entry in value:
        if not isinstance(entry, dict):
            if strict:
                raise ValueError("agent mcpServers entries must be objects")
            continue
        for name, config in entry.items():
            if isinstance(name, str) and isinstance(config, dict):
                servers[name] = config
            elif strict:
                raise ValueError("agent mcpServers entries must map strings to objects")
    return servers
