"""Install-state-aware Claude Code reader for fs-mode scanning.

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
  `attributed_to = "claude-plugin/<name>@<version>"`.

Settings layering is mode-specific:
- `fs` mode reads user + project + local (via `settings_layers.merged("fs")`).
- `repo` mode skips local (machine-local, not CI-relevant).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal, Optional

from tools.component_ref import ComponentRef
from tools.parsers import claude_command_agent, claude_plugin, claude_skill, hooks_json, mcp_json
from tools.parsers.settings_layers import (
    SCOPE_PRECEDENCE,
    SettingsLayers,
)
from tools.parsers.settings_layers import (
    load as load_settings,
)

Mode = Literal["repo", "fs"]


def parse_install(
    install_root: Path,
    project_root: Optional[Path] = None,
    mode: Mode = "fs",
) -> tuple[list[ComponentRef], list[str]]:
    """Read declared+lockfile state and emit one ComponentRef per active plugin.

    Returns `(refs, warnings)`. Warnings are surfaced in `-v` output by the
    `asve-scan fs` command so users see resolver caveats (multi-scope ambiguity,
    missing lockfile entries) without aborting the scan.
    """
    refs: list[ComponentRef] = []
    warnings: list[str] = []

    layers = load_settings(install_root, project_root=project_root)
    effective = layers.merged(mode)

    # Plugin resolution (active plugins + bundled-component walks) only fires
    # when settings declare enabledPlugins AND the lockfile exists. Bare
    # components are walked unconditionally afterwards — a target with no
    # plugins but with bare-scoped MCPs/hooks/skills should still produce
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
        )
        refs.extend(plugin_refs)
        warnings.extend(plugin_walk_warnings)

    refs.extend(_walk_bare_components(install_root, project_root, layers, mode))
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
) -> tuple[list[ComponentRef], list[str]]:
    """Process each enabled plugin: emit self-identity + bundled refs.

    Extracted from `parse_install` so the caller can always reach the
    bare-component walk regardless of plugin-resolution outcomes.
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
        identity = f"claude-plugin/{plugin_name}"
        if version:
            identity = f"{identity}@{version}"

        refs.append(
            ComponentRef(
                ecosystem="claude-plugin",
                name=plugin_name,
                version=version,
                component_identity=identity,
                source_manifest=str(lockfile_path),
                source_locator=f"$.plugins.{plugin_key}[{index}]",
                attributed_to=None,  # plugin itself is direct
                extra={
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
                Path(install_path), plugin_name=plugin_name, attributed_to=identity
            )
            refs.extend(bundled_refs)
            for w in bundled_warnings:
                warnings.append(f"{plugin_key}: {w}")

    return refs, warnings


def _walk_bare_components(
    install_root: Path,
    project_root: Optional[Path],
    layers: SettingsLayers,
    mode: Mode,
) -> list[ComponentRef]:
    """Enumerate components declared outside of any plugin.

    Three surfaces in fs mode:

    1. **Bare MCPs**:
       - `settings.<scope>.mcpServers` (per scope via `by_scope()`).
       - Project-scoped `<project_root>/.mcp.json`.
       - User-scoped `<install_root>/.mcp.json`.

    2. **Bare hooks**: each scope's `hooks` key, emitted with
       `claude-hook/settings/<scope>/...` identity. No cross-scope merging.

    3. **Bare skills**: `<install_root>/skills/<name>/SKILL.md`.

    Scopes obey mode: `repo` mode skips `local` (machine-local, not
    CI-relevant).
    """
    refs: list[ComponentRef] = []
    by_scope = layers.by_scope()
    excluded_scopes: set[str] = {"local"} if mode == "repo" else set()

    # Bare MCPs and hooks from settings, per scope.
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
        # Bare MCPs from settings.mcpServers (inline dict).
        mcp_servers = scope_data.get("mcpServers")
        if isinstance(mcp_servers, dict):
            refs.extend(
                mcp_json.parse_mcp_servers(
                    mcp_servers,
                    source_manifest=str(settings_path),
                    locator_prefix="$.mcpServers (inlined)",
                )
            )
        # Bare hooks per-scope.
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

    # Bare skills at <install_root>/skills/.
    bare_skills_dir = install_root / "skills"
    if bare_skills_dir.is_dir():
        for skill_subdir in sorted(bare_skills_dir.iterdir()):
            skill_md = skill_subdir / "SKILL.md"
            if skill_md.is_file():
                refs.extend(claude_skill.parse(skill_md, attributed_to=None))

    return refs


def _walk_plugin_install_root(
    install_path: Path, plugin_name: str, attributed_to: str
) -> tuple[list[ComponentRef], list[str]]:
    """Enumerate all bundled components inside an active plugin's installPath.

    Surfaces walked (defaults that Claude Code resolves automatically):

    - `<install_path>/.claude-plugin/plugin.json` for inline `mcpServers`,
      string-path mcpServers, and `dependencies` (via
      `claude_plugin.parse_at_install_root`).
    - `<install_path>/.mcp.json` (default MCP path, when plugin.json
      doesn't already point at it).
    - `<install_path>/skills/<name>/SKILL.md` for bundled skills.
    - `<install_path>/hooks/hooks.json` for bundled hooks.
    - `<install_path>/commands/*.md` for bundled slash commands.
    - `<install_path>/agents/*.md` for bundled subagents.

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

    refs.extend(claude_plugin.parse_at_install_root(install_path, attributed_to=attributed_to))

    default_mcp = install_path / ".mcp.json"
    if default_mcp.exists():
        # Avoid double-walking if plugin.json already points at this same file
        # through a string-path mcpServers; dedupe by source_manifest+identity.
        already_seen = {(r.source_manifest, r.component_identity) for r in refs}
        try:
            mcp_refs = mcp_json.parse(default_mcp)
        except Exception:
            mcp_refs = []
        for r in mcp_refs:
            attributed = replace(r, attributed_to=attributed_to)
            if (attributed.source_manifest, attributed.component_identity) not in already_seen:
                refs.append(attributed)

    skills_dir = install_path / "skills"
    if skills_dir.is_dir():
        for skill_subdir in sorted(skills_dir.iterdir()):
            skill_md = skill_subdir / "SKILL.md"
            if skill_md.is_file():
                refs.extend(claude_skill.parse(skill_md, attributed_to=attributed_to))

    hooks_path = install_path / "hooks" / "hooks.json"
    if hooks_path.is_file():
        refs.extend(
            hooks_json.parse_plugin_hooks(
                hooks_path, plugin_name=plugin_name, attributed_to=attributed_to
            )
        )

    refs.extend(
        claude_command_agent.enumerate_dir(
            install_path / "commands",
            kind="command",
            scope_owner=plugin_name,
            attributed_to=attributed_to,
        )
    )
    refs.extend(
        claude_command_agent.enumerate_dir(
            install_path / "agents",
            kind="agent",
            scope_owner=plugin_name,
            attributed_to=attributed_to,
        )
    )

    return refs, warnings


def _split_plugin_key(plugin_key: str) -> tuple[str, Optional[str]]:
    if "@" in plugin_key:
        name, marketplace = plugin_key.rsplit("@", 1)
        return name, marketplace
    return plugin_key, None


def _enabling_scope(plugin_key: str, layers: SettingsLayers, mode: Mode = "fs") -> Optional[str]:
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
