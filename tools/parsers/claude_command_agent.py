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
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    """Emit one ref for a single `*.md` file. Used by the repo-mode
    registry where `rglob` discovers paths individually."""
    if not md_path.is_file() or md_path.suffix != ".md":
        return []
    frontmatter = _read_frontmatter(md_path)
    name = _resolve_name(md_path, frontmatter)
    ecosystem = f"claude-{kind}"
    identity = (
        f"{ecosystem}/{scope_owner}/{name}" if scope_owner is not None else f"{ecosystem}/{name}"
    )
    extra: dict = {"scope_owner": scope_owner, "component_type": kind}
    if runtime_hosts is not None:
        extra["runtime_hosts"] = runtime_hosts
    parent = ComponentRef(
        name=name,
        component_identity=identity,
        source_manifest=str(md_path),
        source_locator="$",
        extra=extra,
    )
    refs = [parent]
    if kind == "agent" and scope_owner is None:
        refs.extend(
            _agent_frontmatter_child_refs(md_path, frontmatter, runtime_hosts=runtime_hosts)
        )
    return refs


def enumerate_dir(
    dir_path: Path,
    kind: Kind,
    scope_owner: Optional[str],
    runtime_hosts: Optional[list[str]] = None,
    contain_within: Optional[Path] = None,
) -> list[ComponentRef]:
    """Walk `dir_path/*.md`, emit one ComponentRef per file.

    `scope_owner` is the plugin name for bundled components, or None for
    repo-declared ones. It is retained in `extra` as observation metadata.
    Parentage is set by the graph edge, not stored on the refs.

    `contain_within`, when given, is a bundle root every walked file must
    stay inside after resolving symlinks — the same containment rule
    `claude_plugin_root._enumerate_bundled_command_agent_dir` applies. Bundle-
    relative callers (a plugin's `commands/` dir) must pass it so a symlinked
    directory or `*.md` escaping the bundle isn't attributed to the plugin;
    install-root/project-root scoped callers have no bundle boundary and omit it.
    """
    if not dir_path.is_dir():
        return []
    contain_resolved: Optional[Path] = None
    if contain_within is not None:
        try:
            contain_resolved = contain_within.resolve()
        except (OSError, RuntimeError):
            return []
    refs: list[ComponentRef] = []
    # Sort for deterministic emission order — makes diffs in fixture
    # snapshots and verbose output stable across runs.
    for child in sorted(dir_path.rglob("*.md")):
        if not child.is_file() or child.suffix != ".md":
            continue
        if contain_resolved is not None:
            try:
                child_resolved = child.resolve()
            except (OSError, RuntimeError):
                continue
            if not child_resolved.is_relative_to(contain_resolved):
                continue
        refs.extend(
            parse_file(child, kind=kind, scope_owner=scope_owner, runtime_hosts=runtime_hosts)
        )
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


def _read_frontmatter(md_path: Path) -> dict:
    try:
        text = md_path.read_text()
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _agent_frontmatter_child_refs(
    md_path: Path,
    frontmatter: dict,
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []

    mcp_servers = _inline_mcp_servers(frontmatter.get("mcpServers"))
    refs.extend(
        mcp_json.parse_mcp_servers(
            mcp_servers,
            source_manifest=str(md_path),
            locator_prefix="$.mcpServers",
            runtime_hosts=runtime_hosts,
        )
    )

    hooks_block = frontmatter.get("hooks")
    if isinstance(hooks_block, dict):
        # Identity scheme follows the OWNING host directory the file lives
        # under (`.claude/agents` vs `.cursor/agents`), not `runtime_hosts`:
        # a `.claude/agents/*.md` file scanned with only Cursor selected
        # (subagent_precedence's cross-host compat-read branch) also gets
        # runtime_hosts=["cursor"] — identical to a genuine
        # `.cursor/agents/*.md` file — so runtime_hosts alone can't tell
        # "Cursor-owned" from "Claude-owned, Cursor merely also reads it"
        # apart. The path can.
        identity_scheme = _hook_identity_scheme_for_agent_path(md_path)
        refs.extend(
            hooks_json.parse_plugin_hooks_inline(
                hooks_block=hooks_block,
                plugin_name="",
                source_manifest=str(md_path),
                runtime_hosts=runtime_hosts,
                identity_scheme=identity_scheme,
            )
        )

    return refs


def _hook_identity_scheme_for_agent_path(md_path: Path) -> str:
    """`"cursor-hook"` for a file under a `.cursor/agents` directory, else
    `"claude-hook"` — mirrors `hooks_json.hook_identity_scheme_for_manifest`'s
    directory-name keying. Walks all ancestors (not just the immediate
    parent) so a subagent nested under `agents/` (`agents.rglob("*.md")`
    allows subdirectories) still resolves to its owning `agents` dir rather
    than an intermediate one.
    """
    parts = md_path.parts
    if "agents" in parts:
        idx = parts.index("agents")
        if idx > 0 and parts[idx - 1] == ".cursor":
            return "cursor-hook"
    return "claude-hook"


def _inline_mcp_servers(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, list):
        return {}
    servers: dict = {}
    for entry in value:
        if not isinstance(entry, dict):
            continue
        for name, config in entry.items():
            if isinstance(name, str) and isinstance(config, dict):
                servers[name] = config
    return servers
