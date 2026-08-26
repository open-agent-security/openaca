"""Shared plugin-root surface walker for repo and endpoint scans."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pathspec import GitIgnoreSpec

from tools.component_ref import ComponentRef
from tools.parsers import claude_command_agent, claude_skill, hooks_json, mcp_json
from tools.parsers.claude_command_agent import Kind
from tools.parsers.gitignore import is_ignored
from tools.parsers.mcp_json import parse_mcp_servers


def walk_plugin_root(
    plugin_root: Path,
    *,
    plugin_name: str,
    plugin_data: dict,
    plugin_json_path: Optional[Path] = None,
) -> list[ComponentRef]:
    """Enumerate plugin-bundled components under a Claude Code plugin root.

    This is used by both repo mode (`<repo>/.claude-plugin/plugin.json`) and
    endpoint mode (`installed_plugins.json[*].installPath`). Parentage is set by
    the graph edge from the plugin node, not stored on the refs.
    """
    if plugin_json_path is None:
        plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"

    refs: list[ComponentRef] = []
    refs.extend(
        _parse_manifest_refs(
            plugin_data,
            plugin_json_path=plugin_json_path,
            plugin_root=plugin_root,
        )
    )
    refs.extend(_parse_default_mcp(plugin_root, refs))
    refs.extend(_parse_bundled_skills(plugin_root, plugin_data))
    refs.extend(
        _parse_bundled_hooks(
            plugin_root, plugin_data, plugin_name, plugin_json_path=plugin_json_path
        )
    )
    refs.extend(_parse_bundled_command_agents(plugin_root, plugin_data, plugin_name))
    return refs


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
    warnings: list[str] | None = None,
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    if "dependencies" in data:
        deps = data["dependencies"]
        if not isinstance(deps, list):
            if warnings is not None:
                warnings.append(f"could not parse {plugin_json_path}: dependencies must be a list")
        else:
            for i, dep in enumerate(deps):
                locator = f"$.dependencies[{i}]"
                if isinstance(dep, str) and dep:
                    refs.append(
                        ComponentRef(
                            name=dep,
                            component_identity=f"plugin-dep/{dep}",
                            source_manifest=str(plugin_json_path),
                            source_locator=locator,
                        )
                    )
                    continue
                if isinstance(dep, dict):
                    name = dep.get("name")
                    version = dep.get("version")
                    if (
                        isinstance(name, str)
                        and name
                        and (version is None or isinstance(version, str))
                    ):
                        ident = f"plugin-dep/{name}"
                        if version:
                            ident = f"{ident}@{version}"
                        refs.append(
                            ComponentRef(
                                name=name,
                                component_identity=ident,
                                source_manifest=str(plugin_json_path),
                                source_locator=locator,
                            )
                        )
                        continue
                if warnings is not None:
                    warnings.append(
                        f"could not parse {plugin_json_path}: {locator} must be a "
                        "dependency string or object with a string name"
                    )

    if "mcpServers" in data:
        servers = data["mcpServers"]
        if isinstance(servers, dict):
            try:
                refs.extend(
                    parse_mcp_servers(
                        servers,
                        source_manifest=str(plugin_json_path),
                        locator_prefix="$.mcpServers (inlined)",
                        strict=warnings is not None,
                    )
                )
            except ValueError as exc:
                if warnings is not None:
                    warnings.append(f"could not parse {plugin_json_path}: {exc}")
        elif isinstance(servers, str):
            referenced = resolve_within(plugin_root, servers)
            if referenced is None or not referenced.is_file():
                if warnings is not None:
                    warnings.append(
                        f"could not parse {servers}: referenced MCP manifest is unavailable"
                    )
            else:
                try:
                    file_refs = mcp_json.parse(referenced, strict=warnings is not None)
                except Exception as exc:
                    if warnings is not None:
                        warnings.append(f"could not parse {referenced}: {exc}")
                    file_refs = []
                refs.extend(file_refs)
        elif warnings is not None:
            warnings.append(
                f"could not parse {plugin_json_path}: mcpServers must be an object or path"
            )
    return refs


def _parse_default_mcp(
    plugin_root: Path,
    existing_refs: list[ComponentRef],
    *,
    warnings: list[str] | None = None,
    mcp_filenames: tuple[str, ...] = (".mcp.json",),
    eval_root: Path | None = None,
    spec: GitIgnoreSpec | None = None,
) -> list[ComponentRef]:
    """Folder discovery for the plugin's bundled MCP manifest.

    `mcp_filenames` is an ORDERED candidate list, not a single name: Cursor's
    folder discovery accepts root `mcp.json` OR `.mcp.json` (both, never
    merged — the first that resolves to a file wins). Claude Code passes a
    one-element tuple, so this is behavior-preserving there.

    `eval_root`/`spec` (default `None` = no filtering, matching endpoint-mode
    callers) exclude a gitignored candidate from the ordered selection BEFORE
    a winner is picked: without this, a gitignored higher-precedence filename
    (e.g. `mcp.json`) would still win here, its refs would then be dropped by
    the caller's final gitignore filter on `ref.source_manifest`, and the
    unignored lower-precedence filename (`.mcp.json`) would never be tried —
    losing the bundled MCP servers entirely instead of falling back to it.
    """
    default_mcp: Path | None = None
    for mcp_filename in mcp_filenames:
        candidate = resolve_within(plugin_root, mcp_filename)
        if candidate is None or not candidate.is_file():
            continue
        if eval_root is not None:
            try:
                rel = candidate.relative_to(eval_root.resolve())
            except (OSError, RuntimeError, ValueError):
                rel = None
            if rel is not None and is_ignored(rel, spec):
                continue
        default_mcp = candidate
        break
    if default_mcp is None:
        return []
    already_seen = {(_source_manifest_key(r), r.component_identity) for r in existing_refs}
    try:
        mcp_refs = mcp_json.parse(default_mcp, strict=warnings is not None)
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"could not parse {default_mcp}: {exc}")
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


def _parse_bundled_skills(plugin_root: Path, data: dict) -> list[ComponentRef]:
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
            refs.extend(claude_skill.parse(skill_md))
    return refs


def _parse_bundled_hooks(
    plugin_root: Path,
    data: dict,
    plugin_name: str,
    *,
    warnings: list[str] | None = None,
    plugin_json_path: Path,
    hooks_filename: str = "hooks/hooks.json",
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    walked_hook_files: set[Path] = set()
    default_hooks = resolve_within(plugin_root, hooks_filename)
    if default_hooks is not None and default_hooks.is_file():
        walked_hook_files.add(default_hooks.resolve())
        try:
            refs.extend(
                hooks_json.parse_plugin_hooks(
                    default_hooks,
                    plugin_name=plugin_name,
                    strict=True,
                )
            )
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"could not parse {default_hooks}: {exc}")
    inline_hooks = data.get("hooks")
    if isinstance(inline_hooks, dict):
        try:
            refs.extend(
                hooks_json.parse_plugin_hooks_inline(
                    hooks_block=inline_hooks,
                    plugin_name=plugin_name,
                    source_manifest=str(plugin_json_path),
                    strict=warnings is not None,
                )
            )
        except ValueError as exc:
            if warnings is not None:
                warnings.append(f"could not parse {plugin_json_path}: {exc}")
    elif isinstance(inline_hooks, str):
        custom_hooks_file = resolve_within(plugin_root, inline_hooks)
        if custom_hooks_file is not None and custom_hooks_file.is_file():
            resolved = custom_hooks_file.resolve()
            if resolved not in walked_hook_files:
                try:
                    refs.extend(
                        hooks_json.parse_plugin_hooks(
                            custom_hooks_file,
                            plugin_name=plugin_name,
                            strict=True,
                        )
                    )
                except Exception as exc:
                    if warnings is not None:
                        warnings.append(f"could not parse {custom_hooks_file}: {exc}")
        elif warnings is not None:
            warnings.append(
                f"could not parse {inline_hooks}: referenced hook manifest is unavailable"
            )
    elif "hooks" in data and warnings is not None:
        warnings.append(f"could not parse {plugin_json_path}: hooks must be an object or path")
    return refs


def _parse_bundled_command_agents(
    plugin_root: Path,
    data: dict,
    plugin_name: str,
    *,
    warnings: list[str] | None = None,
    commands_dir: str = "commands",
    agents_dir: str = "agents",
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    try:
        plugin_root_resolved = plugin_root.resolve()
    except (OSError, RuntimeError):
        return refs
    surfaces: tuple[tuple[Kind, str, str], ...] = (
        ("command", commands_dir, "commands"),
        ("agent", agents_dir, "agents"),
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
                    warnings=warnings,
                )
            )
    return refs


def _enumerate_bundled_command_agent_dir(
    directory: Path,
    *,
    kind: Kind,
    plugin_name: str,
    plugin_root_resolved: Path,
    warnings: list[str] | None = None,
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
        try:
            refs.extend(
                claude_command_agent.parse_file(
                    child,
                    kind=kind,
                    scope_owner=plugin_name,
                    strict=warnings is not None and kind == "agent",
                )
            )
        except Exception as exc:
            if warnings is not None:
                warnings.append(f"could not parse agent definition {child}: {exc}")
    return refs
