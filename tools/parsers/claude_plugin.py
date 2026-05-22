"""Parse .claude-plugin/plugin.json — plugin self-identity, deps, inlined MCP.

Two entry points:

- `parse(path)` — repo-mode: a single `plugin.json` read in isolation, with
  relative paths in `mcpServers` resolved from the plugin root
  (`path.parent.parent`). Emits plugin self-identity, dependencies, MCPs,
  and bundled skills.
- `parse_at_install_root(install_root, attributed_to)` — endpoint mode:
  resolves the same plugin.json from `<install_root>/.claude-plugin/plugin.json`
  with relative paths anchored at `install_root` (CLAUDE_PLUGIN_ROOT
  semantics). Does NOT re-emit the plugin self-identity — caller already
  emits that from `installed_plugins.json` with lockfile-accurate version
  and gitCommitSha — but does emit dependencies and bundled MCPs, all
  tagged with the passed `attributed_to`.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Optional

from tools.component_ref import ComponentRef
from tools.parsers import claude_skill, mcp_json
from tools.parsers.mcp_json import parse_mcp_servers


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    if not isinstance(data, dict):
        return refs

    name = data.get("name")
    version = data.get("version")
    if not isinstance(version, (str, type(None))):
        version = None
    attributed_to = None
    if name:
        component_identity = f"claude-plugin/{name}"
        attributed_to = f"{component_identity}@{version}" if version else component_identity
        refs.append(
            ComponentRef(
                name=name,
                version=version,
                component_identity=component_identity,
                source_manifest=str(path),
                source_locator="$",
                extra={"component_type": "plugin"},
            )
        )

    deps = data.get("dependencies")
    if not isinstance(deps, list):
        deps = []
    for i, dep in enumerate(deps):
        locator = f"$.dependencies[{i}]"
        if isinstance(dep, str):
            refs.append(
                ComponentRef(
                    component_identity=f"claude-plugin-dep/{dep}",
                    source_manifest=str(path),
                    source_locator=locator,
                )
            )
        elif isinstance(dep, dict) and dep.get("name"):
            ident = f"claude-plugin-dep/{dep['name']}"
            if dep.get("version"):
                ident = f"{ident}@{dep['version']}"
            refs.append(
                ComponentRef(
                    component_identity=ident,
                    source_manifest=str(path),
                    source_locator=locator,
                )
            )

    # In repo mode the manifest lives at <plugin-root>/.claude-plugin/plugin.json,
    # so the plugin root is `path.parent.parent`. Endpoint mode goes through
    # `parse_at_install_root` and anchors relative paths at the install root
    # instead — see _parse_mcp_servers_from_plugin_json.
    refs.extend(
        _parse_mcp_servers_from_plugin_json(
            data,
            plugin_json_path=path,
            plugin_root=path.parent.parent,
            attributed_to=attributed_to,
        )
    )
    refs.extend(_parse_bundled_skills(path.parent.parent, data, attributed_to))
    return refs


def parse_at_install_root(install_root: Path, attributed_to: str) -> list[ComponentRef]:
    """Read `<install_root>/.claude-plugin/plugin.json` and emit refs anchored
    at the install root (CLAUDE_PLUGIN_ROOT semantics).

    Does NOT emit the plugin self-identity ref — the endpoint caller emits
    that from `installed_plugins.json` using the lockfile's version and
    gitCommitSha (more accurate than the manifest's declared version).

    Returns:
    - One ref per entry in `dependencies[]`.
    - One ref per server in inline `mcpServers` (dict form).
    - Refs from the referenced `.mcp.json` (string form), with path
      resolution anchored at `install_root` — not the manifest's parent.

    All refs are tagged with the caller-supplied `attributed_to`. Returns
    [] on any read/parse/shape failure so a single bad plugin.json doesn't
    abort the wider scan.
    """
    plugin_json = install_root / ".claude-plugin" / "plugin.json"
    if not plugin_json.exists():
        return []
    try:
        data = json.loads(plugin_json.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    refs: list[ComponentRef] = []
    deps = data.get("dependencies")
    if isinstance(deps, list):
        for i, dep in enumerate(deps):
            locator = f"$.dependencies[{i}]"
            if isinstance(dep, str):
                refs.append(
                    ComponentRef(
                        component_identity=f"claude-plugin-dep/{dep}",
                        source_manifest=str(plugin_json),
                        source_locator=locator,
                        attributed_to=attributed_to,
                    )
                )
            elif isinstance(dep, dict) and dep.get("name"):
                ident = f"claude-plugin-dep/{dep['name']}"
                if dep.get("version"):
                    ident = f"{ident}@{dep['version']}"
                refs.append(
                    ComponentRef(
                        component_identity=ident,
                        source_manifest=str(plugin_json),
                        source_locator=locator,
                        attributed_to=attributed_to,
                    )
                )
    refs.extend(
        _parse_mcp_servers_from_plugin_json(
            data,
            plugin_json_path=plugin_json,
            plugin_root=install_root,
            attributed_to=attributed_to,
        )
    )
    return refs


def _parse_mcp_servers_from_plugin_json(
    data: dict,
    plugin_json_path: Path,
    plugin_root: Path,
    attributed_to: Optional[str],
) -> list[ComponentRef]:
    """Walk `mcpServers` (inline dict or string-path) anchored at `plugin_root`.

    String paths are resolved against `plugin_root` (not the manifest's
    parent). Absolute paths and `..`-traversal that escapes the root are
    rejected. The caller decides what `plugin_root` means:
    - repo mode: `manifest.parent.parent`
    - endpoint mode: the active install root
    """
    refs: list[ComponentRef] = []
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        inline_refs = parse_mcp_servers(
            servers,
            source_manifest=str(plugin_json_path),
            locator_prefix="$.mcpServers (inlined)",
        )
        refs.extend(_with_attribution(inline_refs, attributed_to))
    elif isinstance(servers, str):
        plugin_root_resolved = plugin_root.resolve()
        referenced = (plugin_root / servers).resolve()
        if referenced.is_relative_to(plugin_root_resolved) and referenced.exists():
            try:
                file_refs = mcp_json.parse(referenced)
            except Exception:
                file_refs = []
            refs.extend(_with_attribution(file_refs, attributed_to))
    return refs


def _parse_bundled_skills(
    plugin_root: Path, data: dict, attributed_to: Optional[str]
) -> list[ComponentRef]:
    plugin_root_resolved = plugin_root.resolve()
    skill_dirs: list[Path] = []
    default_skills = _resolve_within(plugin_root, "skills")
    if default_skills is not None and default_skills.is_dir():
        skill_dirs.append(default_skills)
    custom_skills = data.get("skills")
    if isinstance(custom_skills, str):
        custom_dir = _resolve_within(plugin_root, custom_skills)
        if custom_dir is not None and custom_dir.is_dir():
            skill_dirs.append(custom_dir)

    refs: list[ComponentRef] = []
    seen_dirs: set[Path] = set()
    for skills_dir in skill_dirs:
        resolved = skills_dir.resolve()
        if resolved in seen_dirs:
            continue
        seen_dirs.add(resolved)
        try:
            entries = sorted(skills_dir.iterdir())
        except OSError:
            continue
        for skill_subdir in entries:
            try:
                subdir_resolved = skill_subdir.resolve()
            except OSError:
                continue
            if not subdir_resolved.is_relative_to(plugin_root_resolved):
                continue
            skill_md = skill_subdir / "SKILL.md"
            if skill_md.is_file():
                refs.extend(claude_skill.parse(skill_md, attributed_to=attributed_to))
    return refs


def _resolve_within(base: Path, rel: str) -> Optional[Path]:
    if not isinstance(rel, str) or not rel:
        return None
    base_resolved = base.resolve()
    try:
        target = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    if not target.is_relative_to(base_resolved):
        return None
    return target


def _with_attribution(refs: list[ComponentRef], attributed_to: Optional[str]) -> list[ComponentRef]:
    """Rebuild refs with `attributed_to` set. ComponentRef is frozen so we
    use dataclasses.replace; no-op when attribution is None."""
    if attributed_to is None:
        return refs
    return [replace(r, attributed_to=attributed_to) for r in refs]
