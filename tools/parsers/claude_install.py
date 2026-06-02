"""Install-state-aware Claude Code reader for endpoint scanning.

Resolves Claude Code's four-layer install model:

    settings.json.enabledPlugins  (what user wants active)
      ∩
    installed_plugins.json[<key>][<index>]  (what's actually installed,
                                             with version/installPath/SHA)
      → one claude-plugin ComponentRef + walk inside the installPath
         for bundled components (MCPs, skills, hooks, commands, agents)

`installed_plugins.json` indexes by `<plugin>@<marketplace>` keys and the
value is an array of install entries. The schema reserves capacity for
per-scope multiplexing (e.g., separate installs at user vs project scope).
In practice today entries are single-element. When multi-element, prefer
the entry whose `scope` field matches the settings layer that enabled the
plugin; fall back to `[0]` with a warning.

Plan 008 adds:
- Bundled component walking: for each active plugin's installPath, emit
  refs for `.mcp.json`, `skills/<name>/SKILL.md`, `hooks/hooks.json`,
  `commands/*.md`, `agents/*.md`. All bundled refs carry
  `attributed_to = "claude-plugin/<marketplace>/<name>@<version>"` when marketplace is known.

Settings layering is mode-specific:
- `endpoint` mode reads user + project + local.
- `repo` mode skips local (machine-local, not CI-relevant).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal, Optional

from tools.component_ref import ComponentRef
from tools.parsers import (
    bun_lock,
    claude_command_agent,
    claude_skill,
    hooks_json,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    skill_lock,
    uv_lock,
)
from tools.parsers.claude_plugin_root import walk_plugin_root
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.parsers.settings_layers import (
    SCOPE_PRECEDENCE,
    SettingsLayers,
)
from tools.parsers.settings_layers import (
    load as load_settings,
)

Mode = Literal["repo", "endpoint"]


def parse_install(
    install_root: Path,
    project_root: Optional[Path] = None,
    mode: Mode = "endpoint",
    include_transitive: bool = True,
) -> tuple[list[ComponentRef], list[str]]:
    """Read declared+lockfile state and emit one ComponentRef per active plugin.

    Returns `(refs, warnings)`. Warnings are surfaced in `-v` output by the
    `openaca scan endpoint` command so users see resolver caveats (multi-scope
    ambiguity, missing lockfile entries) without aborting the scan.
    """
    refs: list[ComponentRef] = []
    warnings: list[str] = []

    layers = load_settings(install_root, project_root=project_root)
    effective = layers.merged(mode)

    # Plugin resolution (active plugins + bundled-component walks) only fires
    # when settings declare enabledPlugins AND the lockfile exists. Direct
    # components are walked unconditionally afterwards — a target with no
    # plugins but with direct MCPs/hooks/skills should still produce
    # inventory.
    plugins_map, lockfile_path, plugin_warnings = _load_plugins_map(install_root)
    warnings.extend(plugin_warnings)

    enabled_plugins = effective.get("enabledPlugins") or {}
    if isinstance(enabled_plugins, dict) and plugins_map is not None and lockfile_path is not None:
        plugin_refs, plugin_walk_warnings = _walk_active_plugins(
            enabled_plugins=enabled_plugins,
            plugins_map=plugins_map,
            lockfile_path=lockfile_path,
            layers=layers,
            mode=mode,
            include_transitive=include_transitive,
        )
        refs.extend(plugin_refs)
        warnings.extend(plugin_walk_warnings)

    refs.extend(_walk_direct_components(install_root, project_root, layers, mode))
    return refs, warnings


def _load_plugins_map(
    install_root: Path,
) -> tuple[Optional[dict], Optional[Path], list[str]]:
    """Read and validate `<install_root>/plugins/installed_plugins.json`.

    Returns `(plugins_map, lockfile_path, warnings)`. Either both first
    elements are non-None (lockfile parsed cleanly) or both are None.
    The lockfile is optional; a missing file is not a warning.
    """
    warnings: list[str] = []
    lockfile_path = install_root / "plugins" / "installed_plugins.json"
    if not lockfile_path.exists():
        return None, None, warnings
    try:
        lockfile = json.loads(lockfile_path.read_text())
    except json.JSONDecodeError as exc:
        warnings.append(f"installed_plugins.json malformed: {exc}")
        return None, None, warnings
    except OSError as exc:
        warnings.append(f"installed_plugins.json unreadable: {exc}")
        return None, None, warnings
    except UnicodeDecodeError as exc:
        warnings.append(f"installed_plugins.json decode error: {exc}")
        return None, None, warnings
    if not isinstance(lockfile, dict):
        warnings.append("installed_plugins.json: expected an object at top level")
        return None, None, warnings
    plugins_map = lockfile.get("plugins") or {}
    if not isinstance(plugins_map, dict):
        warnings.append("installed_plugins.json: 'plugins' value is not an object")
        return None, None, warnings
    return plugins_map, lockfile_path, warnings


def _walk_active_plugins(
    enabled_plugins: dict,
    plugins_map: dict,
    lockfile_path: Path,
    layers: SettingsLayers,
    mode: Mode,
    include_transitive: bool = True,
) -> tuple[list[ComponentRef], list[str]]:
    """Process each enabled plugin: emit self-identity + bundled refs.

    Extracted from `parse_install` so the caller can always reach the
    direct-component walk regardless of plugin-resolution outcomes.
    """
    refs: list[ComponentRef] = []
    warnings: list[str] = []

    for plugin_key, is_enabled in enabled_plugins.items():
        if is_enabled is not True:
            continue
        raw_entries = plugins_map.get(plugin_key)
        if not isinstance(raw_entries, list) or not raw_entries:
            warnings.append(f"plugin {plugin_key} enabled but missing from installed_plugins.json")
            continue
        # Preserve original lockfile-array indices when filtering out malformed
        # (non-dict) entries; source_locator must reference the real slot in
        # installed_plugins.json so findings + debugging evidence are accurate
        # even when a malformed element precedes the chosen one.
        entries: list[tuple[int, dict]] = [
            (i, e) for i, e in enumerate(raw_entries) if isinstance(e, dict)
        ]
        if not entries:
            warnings.append(f"plugin {plugin_key}: no valid install entries; skipping")
            continue

        scope = _enabling_scope(plugin_key, layers, mode)
        entry, index, warning = _select_install_entry(entries, scope)
        if warning is not None:
            warnings.append(f"{plugin_key}: {warning}")

        plugin_name, marketplace = _split_plugin_key(plugin_key)
        version = entry.get("version")
        if version is not None and not isinstance(version, str):
            warnings.append(
                f"{plugin_key}: non-string version {version!r} in installed_plugins.json; skipping"
            )
            continue
        # Canonical component_identity is version-less so advisory matching is
        # consistent whether the plugin was discovered in repo mode (settings.json,
        # no version) or endpoint mode (installed_plugins.json, version known).
        # The versioned attributed_id is used only for bundled-component attribution.
        component_identity = _plugin_identity(plugin_name, marketplace)
        attributed_id = f"{component_identity}@{version}" if version else component_identity

        refs.append(
            ComponentRef(
                name=plugin_name,
                version=version,
                component_identity=component_identity,
                source_manifest=str(lockfile_path),
                source_locator=f"$.plugins.{plugin_key}[{index}]",
                attributed_to=None,  # plugin itself is direct
                extra={
                    "component_type": "plugin",
                    "runtime_hosts": ["claude-code"],
                    "declared_by": {"kind": "skill_lock", "path": str(lockfile_path)},
                    "component_path": [{"type": "plugin", "name": plugin_name}],
                    "gitCommitSha": entry.get("gitCommitSha"),
                    "installPath": entry.get("installPath"),
                    "marketplace": marketplace,
                    "scope": entry.get("scope"),
                },
            )
        )

        install_path = entry.get("installPath")
        if isinstance(install_path, str) and install_path:
            bundled_refs, bundled_warnings = _walk_plugin_install_root(
                Path(install_path), plugin_name=plugin_name, attributed_to=attributed_id
            )
            refs.extend(bundled_refs)
            for w in bundled_warnings:
                warnings.append(f"{plugin_key}: {w}")

            if include_transitive:
                tier2_refs = _walk_plugin_implementation_deps(
                    Path(install_path), attributed_to=attributed_id
                )
                refs.extend(tier2_refs)

    return refs, warnings


def _walk_direct_components(
    install_root: Path,
    project_root: Optional[Path],
    layers: SettingsLayers,
    mode: Mode,
) -> list[ComponentRef]:
    """Enumerate components declared outside of any plugin.

    Three surfaces in endpoint mode:

    1. **Direct MCPs**:
       - `settings.<scope>.mcpServers` (per scope via `by_scope()`).
       - Project-scoped `<project_root>/.mcp.json`.
       - User-scoped `<install_root>/.mcp.json`.

    2. **Direct hooks**: each scope's `hooks` key, emitted with
       `claude-hook/settings/<scope>/...` identity. No cross-scope merging.

    3. **Direct skills/commands/agents**:
       - Personal `<install_root>/skills/<name>/SKILL.md`.
       - Personal `<install_root>/commands/**/*.md`.
       - Personal `<install_root>/agents/**/*.md`.
       - Project `**/.claude/skills/<name>/SKILL.md`.
       - Project `.claude/commands/**/*.md`.
       - Project `.claude/agents/**/*.md`.

    Scopes obey mode: `repo` mode skips `local` (machine-local, not
    CI-relevant).
    """
    refs: list[ComponentRef] = []
    by_scope = layers.by_scope()
    excluded_scopes: set[str] = {"local"} if mode == "repo" else set()

    # Direct MCPs and hooks from settings, per scope.
    scope_to_settings_path = {
        "user": install_root / "settings.json",
        "project": (project_root / ".claude" / "settings.json")
        if project_root is not None
        else None,
        "local": (project_root / ".claude" / "settings.local.json")
        if project_root is not None
        else None,
    }
    for scope in SCOPE_PRECEDENCE:
        if scope == "managed" or scope in excluded_scopes:
            continue
        scope_data = by_scope.get(scope) or {}
        settings_path = scope_to_settings_path.get(scope)
        if settings_path is None:
            continue
        # Direct MCPs from settings.mcpServers (inline dict).
        mcp_servers = scope_data.get("mcpServers")
        if isinstance(mcp_servers, dict):
            refs.extend(
                mcp_json.parse_mcp_servers(
                    mcp_servers,
                    source_manifest=str(settings_path),
                    locator_prefix="$.mcpServers (inlined)",
                )
            )
        # Direct hooks per-scope.
        hooks_block = scope_data.get("hooks")
        refs.extend(hooks_json.parse_settings_hooks(settings_path, hooks_block, scope=scope))

    # Project-scoped .mcp.json at the repo root.
    if project_root is not None:
        project_mcp = project_root / ".mcp.json"
        if project_mcp.is_file():
            try:
                refs.extend(mcp_json.parse(project_mcp))
            except Exception:
                pass

    # User-scoped .mcp.json at the install root (rare but legal).
    user_mcp = install_root / ".mcp.json"
    if user_mcp.is_file():
        try:
            refs.extend(mcp_json.parse(user_mcp))
        except Exception:
            pass

    # Direct skills at <install_root>/skills/.
    direct_skills_dir = install_root / "skills"
    refs.extend(_walk_skill_dir(direct_skills_dir, project_root=project_root))
    refs.extend(
        claude_command_agent.enumerate_dir(
            install_root / "commands",
            kind="command",
            scope_owner=None,
            attributed_to=None,
        )
    )
    refs.extend(
        claude_command_agent.enumerate_dir(
            install_root / "agents",
            kind="agent",
            scope_owner=None,
            attributed_to=None,
        )
    )

    if project_root is not None:
        refs.extend(_walk_project_skill_dirs(project_root))
        refs.extend(
            claude_command_agent.enumerate_dir(
                project_root / ".claude" / "commands",
                kind="command",
                scope_owner=None,
                attributed_to=None,
            )
        )
        refs.extend(
            claude_command_agent.enumerate_dir(
                project_root / ".claude" / "agents",
                kind="agent",
                scope_owner=None,
                attributed_to=None,
            )
        )

    return refs


def _walk_skill_dir(skills_dir: Path, project_root: Optional[Path] = None) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    if not skills_dir.is_dir():
        return refs
    for skill_subdir in sorted(skills_dir.iterdir()):
        skill_md = skill_subdir / "SKILL.md"
        if skill_md.is_file():
            refs.extend(_parse_direct_skill(skill_md, project_root=project_root))
    return refs


def _parse_direct_skill(skill_md: Path, project_root: Optional[Path]) -> list[ComponentRef]:
    refs = claude_skill.parse(skill_md, attributed_to=None)
    out: list[ComponentRef] = []
    for ref in refs:
        if not ref.name:
            out.append(ref)
            continue
        provenance = skill_lock.provenance_for_skill(skill_md, ref.name, project_root=project_root)
        if provenance is None:
            out.append(ref)
            continue
        extra = dict(ref.extra)
        extra["source_provenance"] = provenance
        out.append(replace(ref, extra=extra))
    return out


def _is_project_skill_file(path: Path, project_root: Path) -> bool:
    try:
        rel = path.relative_to(project_root)
    except ValueError:
        rel = path
    parts = rel.parts
    if len(parts) < 4 or parts[-1] != "SKILL.md":
        return False
    return any(
        parts[i] == ".claude"
        and i + 3 < len(parts)
        and parts[i + 1] == "skills"
        and i + 3 == len(parts) - 1
        for i in range(len(parts) - 3)
    )


def _walk_project_skill_dirs(project_root: Path) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    refs.extend(_walk_skill_dir(project_root / ".claude" / "skills", project_root=project_root))
    seen = {(r.source_manifest, r.component_identity) for r in refs}

    spec = load_gitignore_spec(project_root)
    for skill_md in iter_unignored_files(project_root, spec):
        if not _is_project_skill_file(skill_md, project_root):
            continue
        for ref in _parse_direct_skill(skill_md, project_root=project_root):
            key = (ref.source_manifest, ref.component_identity)
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
    return refs


def _walk_plugin_install_root(
    install_path: Path, plugin_name: str, attributed_to: str
) -> tuple[list[ComponentRef], list[str]]:
    """Enumerate all bundled components inside an active plugin's installPath.

    Surfaces walked. Defaults Claude Code resolves automatically AND any
    custom paths declared in plugin.json — both are walked because defaults
    *merge with*, not replace, custom paths. Dedup is by resolved absolute
    path: when a plugin declares a custom path that points at the default
    (e.g., `"skills": "./skills/"`) we walk it once.

    - `<install_path>/.claude-plugin/plugin.json` for inline `mcpServers`,
      string-path mcpServers, and `dependencies`.
    - `<install_path>/.mcp.json` (default MCP path, when plugin.json
      doesn't already point at it).
    - `<install_path>/skills/<name>/SKILL.md` (default) plus
      `plugin.json["skills"]` if it's a string-path to a skills directory.
    - `<install_path>/hooks/hooks.json` (default) plus `plugin.json["hooks"]`
      as either an inline dict (same inner shape as hooks.json) or a
      string-path to a hooks.json file.
    - `<install_path>/commands/*.md` plus `plugin.json["commands"]` string-path.
    - `<install_path>/agents/*.md` plus `plugin.json["agents"]` string-path.

    All emitted refs carry the caller-supplied `attributed_to`. Missing
    surfaces are not warnings — most plugins have only a subset. Returns
    `(refs, warnings)` so the caller can surface non-fatal anomalies
    (e.g., installPath doesn't exist) in verbose output.
    """
    refs: list[ComponentRef] = []
    warnings: list[str] = []

    # Silent return for missing installPath: orphaned/stale lockfile entries
    # are surfaced via zero bundled counts in verbose output, not warnings.
    if not install_path.is_dir():
        return refs, warnings

    # Read plugin.json once to drive custom-path handling for every surface.
    plugin_data: dict = {}
    plugin_json_path = install_path / ".claude-plugin" / "plugin.json"
    if plugin_json_path.is_file():
        try:
            loaded = json.loads(plugin_json_path.read_text())
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            plugin_data = loaded

    refs.extend(
        walk_plugin_root(
            install_path,
            plugin_name=plugin_name,
            plugin_data=plugin_data,
            plugin_json_path=plugin_json_path,
            attributed_to=attributed_to,
        )
    )

    return (
        _with_plugin_context(
            refs,
            plugin_name=plugin_name,
            plugin_manifest_path=plugin_json_path,
        ),
        warnings,
    )


# (ecosystem, lockfile_filename, parser_callable) — parsed in order; multiple
# ecosystems can coexist (a single plugin can ship JS + embedded Python).
# For same-ecosystem lockfiles (bun.lock / package-lock.json both npm), only
# the first match is used — bun.lock takes priority as the authoritative
# lockfile for Bun-migrated projects.
_LOCKFILE_DISPATCH: list[tuple[str, str, object]] = [
    ("npm", "bun.lock", bun_lock.parse),
    ("npm", "package-lock.json", package_lock_json.parse),
    ("PyPI", "uv.lock", uv_lock.parse),
]

# Manifest fallback runs ONLY for ecosystems not already covered by a lockfile.
_MANIFEST_FALLBACK: list[tuple[str, str, object]] = [
    ("npm", "package.json", package_json.parse),
    ("PyPI", "pyproject.toml", pyproject_toml.parse),
]

# Restrict manifest fallback to runtime-only source_locators per ecosystem.
# Absent from this dict → no filtering (all locators accepted).
_RUNTIME_MANIFEST_LOCATORS: dict[str, set[str]] = {
    "npm": {"dependencies"},
    "PyPI": {"project.dependencies"},
}


def _walk_plugin_implementation_deps(install_path: Path, attributed_to: str) -> list[ComponentRef]:
    """Tier-2 walk: parse every supported lockfile at the installPath, then
    manifest-fall-back for ecosystems not covered by a lockfile.

    ADR-0008: lockfile = full transitive; manifest fallback = direct deps
    only with extra["transitive"]=False. For each ecosystem, only the first
    matching lockfile in _LOCKFILE_DISPATCH is used — same-ecosystem lockfiles
    (e.g. bun.lock + package-lock.json) are not double-parsed. Multi-language
    plugins still emit refs for every ecosystem they cover.
    All emissions tagged with the caller-supplied attributed_to.
    """
    if not install_path.is_dir():
        return []
    refs: list[ComponentRef] = []
    covered: set[str] = set()
    for ecosystem, filename, parser in _LOCKFILE_DISPATCH:
        if ecosystem in covered:
            continue  # already have a lockfile for this ecosystem; skip the redundant one
        lockfile = install_path / filename
        if not lockfile.is_file():
            continue
        try:
            lock_refs = parser(lockfile)  # type: ignore[operator]
        except Exception:
            continue
        for r in lock_refs:
            refs.append(replace(r, attributed_to=attributed_to))
        if lock_refs:
            covered.add(ecosystem)
    for ecosystem, filename, parser in _MANIFEST_FALLBACK:
        if ecosystem in covered:
            continue
        manifest = install_path / filename
        if not manifest.is_file():
            continue
        try:
            manifest_refs = parser(manifest)  # type: ignore[operator]
        except Exception:
            continue
        runtime_locators = _RUNTIME_MANIFEST_LOCATORS.get(ecosystem)
        for r in manifest_refs:
            if runtime_locators is not None and r.source_locator not in runtime_locators:
                continue
            extra = dict(r.extra)
            extra["transitive"] = False
            extra["fallback_reason"] = f"no {ecosystem} lockfile present"
            refs.append(replace(r, attributed_to=attributed_to, extra=extra))
    return refs


def _with_plugin_context(
    refs: list[ComponentRef],
    plugin_name: str,
    plugin_manifest_path: Path,
) -> list[ComponentRef]:
    out: list[ComponentRef] = []
    plugin_node = {"type": "plugin", "name": plugin_name}
    for ref in refs:
        child_type = _component_type_for_child(ref)
        child_name = _component_name_for_child(ref)
        extra = dict(ref.extra)
        extra["component_type"] = child_type
        extra["runtime_hosts"] = ["claude-code"]
        extra["declared_by"] = {
            "kind": "plugin",
            "name": plugin_name,
            "path": str(plugin_manifest_path),
        }
        extra["component_path"] = [plugin_node, {"type": child_type, "name": child_name}]
        out.append(replace(ref, extra=extra))
    return out


def _component_type_for_child(ref: ComponentRef) -> str:
    extra_type = ref.extra.get("component_type")
    if isinstance(extra_type, str) and extra_type:
        return extra_type
    return "component"


def _component_name_for_child(ref: ComponentRef) -> str:
    component_path = ref.extra.get("component_path")
    if isinstance(component_path, list) and component_path:
        last = component_path[-1]
        if isinstance(last, dict) and isinstance(last.get("name"), str):
            return last["name"]
    if ref.name:
        return ref.name
    if ref.component_identity:
        return ref.component_identity
    return "<unidentified>"


def _split_plugin_key(plugin_key: str) -> tuple[str, Optional[str]]:
    if "@" in plugin_key:
        name, marketplace = plugin_key.rsplit("@", 1)
        return name, marketplace or None
    return plugin_key, None


def _plugin_identity(plugin_name: str, marketplace: Optional[str]) -> str:
    if marketplace:
        return f"claude-plugin/{marketplace}/{plugin_name}"
    return f"claude-plugin/{plugin_name}"


def _enabling_scope(
    plugin_key: str, layers: SettingsLayers, mode: Mode = "endpoint"
) -> Optional[str]:
    """Return the highest-precedence scope where the plugin is set true.

    Used to break ties in `installed_plugins.json` arrays: when multiple
    entries exist, prefer the install whose `scope` field matches the scope
    that enabled it. In repo mode, local scope is excluded (machine-local,
    not CI-relevant) — consistent with how `merged(mode)` filters settings.
    """
    by_scope = layers.by_scope()
    excluded: set[str] = {"local"} if mode == "repo" else set()
    for scope in SCOPE_PRECEDENCE:
        if scope in excluded:
            continue
        scope_data = by_scope.get(scope, {})
        enabled = scope_data.get("enabledPlugins", {})
        if isinstance(enabled, dict) and enabled.get(plugin_key) is True:
            return scope
    return None


def _select_install_entry(
    entries: list[tuple[int, dict]], enabling_scope: Optional[str]
) -> tuple[dict, int, Optional[str]]:
    """Pick the install entry to emit a component for.

    `entries` is a list of `(original_index, entry)` pairs; the returned
    `index` preserves the original position in `installed_plugins.json` so
    `source_locator` references the real lockfile slot regardless of any
    malformed entries that were filtered out upstream.

    Single-element list (the common case): take it.
    Multi-element list: prefer the entry whose `scope` matches the enabling
    scope; fall back to the first remaining entry with a warning.
    """
    if len(entries) == 1:
        idx, entry = entries[0]
        return entry, idx, None
    if enabling_scope is not None:
        for idx, entry in entries:
            if entry.get("scope") == enabling_scope:
                return entry, idx, None
    warning = (
        f"plugin has {len(entries)} installed entries with no scope match; "
        "taking the first remaining"
    )
    idx, entry = entries[0]
    return entry, idx, warning
