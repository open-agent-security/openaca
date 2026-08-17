"""Shared plugin-root surface walker for repo and endpoint scans."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from tools.component_ref import ComponentRef
from tools.parsers import claude_command_agent, claude_skill, hooks_json, mcp_json
from tools.parsers.claude_command_agent import Kind
from tools.parsers.mcp_json import parse_mcp_servers


def walk_plugin_root(
    plugin_root: Path,
    *,
    plugin_name: str,
    plugin_data: dict,
    plugin_json_path: Optional[Path] = None,
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    """Enumerate plugin-bundled components under a Claude Code or Cursor
    Plugin root.

    This is used by both repo mode (`<repo>/.claude-plugin/plugin.json` or
    `<repo>/.cursor-plugin/plugin.json`) and endpoint mode
    (`installed_plugins.json[*].installPath`, Claude-only). Parentage is set
    by the graph edge from the plugin node, not stored on the refs.

    `runtime_hosts`, when given, is stamped onto every emitted ref (self-
    identity excluded — that ref is built by the caller) and defaults to
    `["claude-code"]` at the caller (`claude_plugin.parse`) when omitted.
    """
    if plugin_json_path is None:
        plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"

    default_filename = default_mcp_filename_for_manifest(plugin_json_path)

    refs: list[ComponentRef] = []
    refs.extend(
        _parse_manifest_refs(
            plugin_data,
            plugin_json_path=plugin_json_path,
            plugin_root=plugin_root,
            runtime_hosts=runtime_hosts,
        )
    )
    refs.extend(
        _parse_default_mcp(
            plugin_root,
            refs,
            default_filename=default_filename,
            runtime_hosts=runtime_hosts,
        )
    )
    refs.extend(_parse_bundled_skills(plugin_root, plugin_data, runtime_hosts=runtime_hosts))
    refs.extend(
        _parse_bundled_hooks(
            plugin_root,
            plugin_data,
            plugin_name,
            plugin_json_path=plugin_json_path,
            runtime_hosts=runtime_hosts,
        )
    )
    refs.extend(
        _parse_bundled_command_agents(
            plugin_root, plugin_data, plugin_name, runtime_hosts=runtime_hosts
        )
    )
    return refs


def default_mcp_filename_for_manifest(plugin_json_path: Path) -> str:
    """`"mcp.json"` for a `.cursor-plugin/plugin.json` bundle (Cursor's
    verified default bundled MCP manifest, spec's Plugins section), else
    `".mcp.json"` (Claude's default)."""
    if plugin_json_path.parent.name == ".cursor-plugin":
        return "mcp.json"
    return ".mcp.json"


def resolve_within(base: Path, rel: str) -> Optional[Path]:
    if not isinstance(rel, str) or not rel:
        return None
    try:
        base_resolved = base.resolve()
        target = (base / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not target.is_relative_to(base_resolved):
        return None
    return target


def _parse_manifest_refs(
    data: dict,
    *,
    plugin_json_path: Path,
    plugin_root: Path,
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    deps = data.get("dependencies")
    if isinstance(deps, list):
        for i, dep in enumerate(deps):
            locator = f"$.dependencies[{i}]"
            if isinstance(dep, str):
                extra: dict = {}
                if runtime_hosts is not None:
                    extra["runtime_hosts"] = runtime_hosts
                refs.append(
                    ComponentRef(
                        name=dep,
                        component_identity=f"plugin-dep/{dep}",
                        source_manifest=str(plugin_json_path),
                        source_locator=locator,
                        extra=extra,
                    )
                )
            elif isinstance(dep, dict) and dep.get("name"):
                ident = f"plugin-dep/{dep['name']}"
                if dep.get("version"):
                    ident = f"{ident}@{dep['version']}"
                extra = {}
                if runtime_hosts is not None:
                    extra["runtime_hosts"] = runtime_hosts
                refs.append(
                    ComponentRef(
                        name=str(dep["name"]),
                        component_identity=ident,
                        source_manifest=str(plugin_json_path),
                        source_locator=locator,
                        extra=extra,
                    )
                )

    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        refs.extend(
            parse_mcp_servers(
                servers,
                source_manifest=str(plugin_json_path),
                locator_prefix="$.mcpServers (inlined)",
                runtime_hosts=runtime_hosts,
            )
        )
    elif isinstance(servers, str):
        referenced = resolve_within(plugin_root, servers)
        if referenced is not None and referenced.exists():
            try:
                file_refs = mcp_json.parse(referenced, runtime_hosts=runtime_hosts)
            except Exception:
                file_refs = []
            refs.extend(file_refs)
    return refs


def _parse_default_mcp(
    plugin_root: Path,
    existing_refs: list[ComponentRef],
    *,
    default_filename: str = ".mcp.json",
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    default_mcp = resolve_within(plugin_root, default_filename)
    if default_mcp is None or not default_mcp.is_file():
        return []
    already_seen = {(_source_manifest_key(r), r.component_identity) for r in existing_refs}
    try:
        mcp_refs = mcp_json.parse(default_mcp, runtime_hosts=runtime_hosts)
    except Exception:
        return []
    out: list[ComponentRef] = []
    for ref in mcp_refs:
        if (_source_manifest_key(ref), ref.component_identity) not in already_seen:
            out.append(ref)
    return out


def _source_manifest_key(ref: ComponentRef) -> str:
    if not ref.source_manifest:
        return ""
    try:
        return str(Path(ref.source_manifest).resolve())
    except (OSError, RuntimeError, ValueError):
        return ref.source_manifest


def _parse_bundled_skills(
    plugin_root: Path, data: dict, *, runtime_hosts: Optional[list[str]] = None
) -> list[ComponentRef]:
    try:
        plugin_root_resolved = plugin_root.resolve()
    except (OSError, RuntimeError):
        return []
    skill_dirs: list[Path] = []
    default_skills = resolve_within(plugin_root, "skills")
    if default_skills is not None and default_skills.is_dir():
        skill_dirs.append(default_skills)
    custom_skills = data.get("skills")
    if isinstance(custom_skills, str):
        custom_dir = resolve_within(plugin_root, custom_skills)
        if custom_dir is not None and custom_dir.is_dir():
            skill_dirs.append(custom_dir)

    refs: list[ComponentRef] = []
    seen_dirs: set[Path] = set()
    for skills_dir in skill_dirs:
        try:
            resolved = skills_dir.resolve()
        except (OSError, RuntimeError):
            continue
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
            except (OSError, RuntimeError):
                continue
            if not subdir_resolved.is_relative_to(plugin_root_resolved):
                continue
            skill_md = skill_subdir / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                skill_md_resolved = skill_md.resolve()
            except (OSError, RuntimeError):
                continue
            if not skill_md_resolved.is_relative_to(plugin_root_resolved):
                continue
            refs.extend(claude_skill.parse(skill_md, runtime_hosts=runtime_hosts))
    return refs


def _parse_bundled_hooks(
    plugin_root: Path,
    data: dict,
    plugin_name: str,
    *,
    plugin_json_path: Path,
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    identity_scheme = hooks_json.hook_identity_scheme_for_manifest(plugin_json_path)
    refs: list[ComponentRef] = []
    walked_hook_files: set[Path] = set()
    default_hooks = resolve_within(plugin_root, "hooks/hooks.json")
    if default_hooks is not None and default_hooks.is_file():
        walked_hook_files.add(default_hooks.resolve())
        refs.extend(
            hooks_json.parse_plugin_hooks(
                default_hooks,
                plugin_name=plugin_name,
                runtime_hosts=runtime_hosts,
                identity_scheme=identity_scheme,
            )
        )
    inline_hooks = data.get("hooks")
    if isinstance(inline_hooks, dict):
        refs.extend(
            hooks_json.parse_plugin_hooks_inline(
                hooks_block=inline_hooks,
                plugin_name=plugin_name,
                source_manifest=str(plugin_json_path),
                runtime_hosts=runtime_hosts,
                identity_scheme=identity_scheme,
            )
        )
    elif isinstance(inline_hooks, str):
        custom_hooks_file = resolve_within(plugin_root, inline_hooks)
        if custom_hooks_file is not None and custom_hooks_file.is_file():
            resolved = custom_hooks_file.resolve()
            if resolved not in walked_hook_files:
                refs.extend(
                    hooks_json.parse_plugin_hooks(
                        custom_hooks_file,
                        plugin_name=plugin_name,
                        runtime_hosts=runtime_hosts,
                        identity_scheme=identity_scheme,
                    )
                )
    return refs


def _parse_bundled_command_agents(
    plugin_root: Path,
    data: dict,
    plugin_name: str,
    *,
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    try:
        plugin_root_resolved = plugin_root.resolve()
    except (OSError, RuntimeError):
        return refs
    surfaces: tuple[tuple[Kind, str, str], ...] = (
        ("command", "commands", "commands"),
        ("agent", "agents", "agents"),
    )
    for kind, default_subdir, plugin_key in surfaces:
        dirs: list[Path] = []
        default_dir = resolve_within(plugin_root, default_subdir)
        if default_dir is not None and default_dir.is_dir():
            dirs.append(default_dir)
        custom = data.get(plugin_key)
        if isinstance(custom, str):
            custom_dir = resolve_within(plugin_root, custom)
            if custom_dir is not None and custom_dir.is_dir():
                dirs.append(custom_dir)
        seen_dirs: set[Path] = set()
        for directory in dirs:
            resolved = directory.resolve()
            if resolved in seen_dirs:
                continue
            seen_dirs.add(resolved)
            refs.extend(
                _enumerate_bundled_command_agent_dir(
                    directory,
                    kind=kind,
                    plugin_name=plugin_name,
                    plugin_root_resolved=plugin_root_resolved,
                    runtime_hosts=runtime_hosts,
                )
            )
    return refs


def _enumerate_bundled_command_agent_dir(
    directory: Path,
    *,
    kind: Kind,
    plugin_name: str,
    plugin_root_resolved: Path,
    runtime_hosts: Optional[list[str]] = None,
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    try:
        children = sorted(directory.rglob("*.md"))
    except OSError:
        return refs
    for child in children:
        try:
            child_resolved = child.resolve()
        except (OSError, RuntimeError):
            continue
        if not child_resolved.is_relative_to(plugin_root_resolved):
            continue
        refs.extend(
            claude_command_agent.parse_file(
                child,
                kind=kind,
                scope_owner=plugin_name,
                runtime_hosts=runtime_hosts,
            )
        )
    return refs
