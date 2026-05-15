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
  `attributed_to = "claude-plugin/<name>@<version>"`.

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
    claude_command_agent,
    claude_plugin,
    claude_skill,
    hooks_json,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    uv_lock,
)
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
            include_transitive=include_transitive,
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
    include_transitive: bool = True,
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

            if include_transitive:
                tier2_refs = _walk_plugin_implementation_deps(
                    Path(install_path), attributed_to=identity
                )
                refs.extend(tier2_refs)

    return refs, warnings


def _walk_bare_components(
    install_root: Path,
    project_root: Optional[Path],
    layers: SettingsLayers,
    mode: Mode,
) -> list[ComponentRef]:
    """Enumerate components declared outside of any plugin.

    Three surfaces in endpoint mode:

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


def _resolve_within(base: Path, rel: str) -> Optional[Path]:
    """Resolve `rel` against `base`, rejecting empty/non-string input and any
    target that escapes `base` after `..` resolution.

    Used for CLAUDE_PLUGIN_ROOT-relative custom paths in plugin.json. An
    absolute path or one that climbs out of the install root returns None so
    a malicious or malformed manifest can't redirect the walker outside the
    plugin's own tree.
    """
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
      string-path mcpServers, and `dependencies` (via
      `claude_plugin.parse_at_install_root`).
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

    refs.extend(claude_plugin.parse_at_install_root(install_path, attributed_to=attributed_to))

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

    # Skills: default <install>/skills + plugin.json["skills"] string path.
    # Dedupe by resolved directory path so supabase-style
    # `"skills": "./skills/"` doesn't double-emit.
    skill_dirs: list[Path] = []
    default_skills_dir = install_path / "skills"
    if default_skills_dir.is_dir():
        skill_dirs.append(default_skills_dir)
    custom_skills = plugin_data.get("skills")
    if isinstance(custom_skills, str):
        custom_skills_dir = _resolve_within(install_path, custom_skills)
        if custom_skills_dir is not None and custom_skills_dir.is_dir():
            skill_dirs.append(custom_skills_dir)
    seen_skill_dirs: set[Path] = set()
    for skills_dir in skill_dirs:
        resolved = skills_dir.resolve()
        if resolved in seen_skill_dirs:
            continue
        seen_skill_dirs.add(resolved)
        for skill_subdir in sorted(skills_dir.iterdir()):
            skill_md = skill_subdir / "SKILL.md"
            if skill_md.is_file():
                refs.extend(claude_skill.parse(skill_md, attributed_to=attributed_to))

    # Hooks: default hooks/hooks.json plus plugin.json["hooks"] inline dict
    # OR string-path. Dedupe file-walk by resolved path so a custom string
    # that lands on the default file doesn't double-emit.
    walked_hook_files: set[Path] = set()
    default_hooks = install_path / "hooks" / "hooks.json"
    if default_hooks.is_file():
        walked_hook_files.add(default_hooks.resolve())
        refs.extend(
            hooks_json.parse_plugin_hooks(
                default_hooks, plugin_name=plugin_name, attributed_to=attributed_to
            )
        )
    inline_hooks = plugin_data.get("hooks")
    if isinstance(inline_hooks, dict):
        refs.extend(
            hooks_json.parse_plugin_hooks_inline(
                hooks_block=inline_hooks,
                plugin_name=plugin_name,
                source_manifest=str(plugin_json_path),
                attributed_to=attributed_to,
            )
        )
    elif isinstance(inline_hooks, str):
        custom_hooks_file = _resolve_within(install_path, inline_hooks)
        if custom_hooks_file is not None and custom_hooks_file.is_file():
            resolved = custom_hooks_file.resolve()
            if resolved not in walked_hook_files:
                walked_hook_files.add(resolved)
                refs.extend(
                    hooks_json.parse_plugin_hooks(
                        custom_hooks_file,
                        plugin_name=plugin_name,
                        attributed_to=attributed_to,
                    )
                )

    # Commands: default <install>/commands + plugin.json["commands"] string path.
    for kind, default_subdir, plugin_key in (
        ("command", "commands", "commands"),
        ("agent", "agents", "agents"),
    ):
        dirs: list[Path] = []
        default_dir = install_path / default_subdir
        if default_dir.is_dir():
            dirs.append(default_dir)
        custom = plugin_data.get(plugin_key)
        if isinstance(custom, str):
            custom_dir = _resolve_within(install_path, custom)
            if custom_dir is not None and custom_dir.is_dir():
                dirs.append(custom_dir)
        seen_dirs: set[Path] = set()
        for d in dirs:
            resolved = d.resolve()
            if resolved in seen_dirs:
                continue
            seen_dirs.add(resolved)
            refs.extend(
                claude_command_agent.enumerate_dir(
                    d,
                    kind=kind,  # type: ignore[arg-type]
                    scope_owner=plugin_name,
                    attributed_to=attributed_to,
                )
            )

    return refs, warnings


# (ecosystem, lockfile_filename, parser_callable) — parsed in order; multiple
# ecosystems can coexist (a single plugin can ship JS + embedded Python).
_LOCKFILE_DISPATCH: list[tuple[str, str, object]] = [
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
    only with extra["transitive"]=False. Parse ALL supported lockfiles, not
    first-match, so multi-language plugins emit refs for every ecosystem.
    All emissions tagged with the caller-supplied attributed_to.
    """
    if not install_path.is_dir():
        return []
    refs: list[ComponentRef] = []
    covered: set[str] = set()
    for ecosystem, filename, parser in _LOCKFILE_DISPATCH:
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


def _split_plugin_key(plugin_key: str) -> tuple[str, Optional[str]]:
    if "@" in plugin_key:
        name, marketplace = plugin_key.rsplit("@", 1)
        return name, marketplace
    return plugin_key, None


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
