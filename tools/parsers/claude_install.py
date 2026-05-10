"""Install-state-aware Claude Code reader for fs-mode scanning.

Plan 007 scope (this file): emit one ComponentRef per active plugin from the
intersection of `settings.json`'s `enabledPlugins` and
`plugins/installed_plugins.json`. No walking inside plugin install paths;
that's plan 008. No plugin-internal lockfile scanning; that's plan 009.

The resolver follows Claude Code's four-layer install model:

    settings.json.enabledPlugins  (what user wants active)
      ∩
    installed_plugins.json[<key>][<index>]  (what's actually installed,
                                             with version/installPath/SHA)
      → emit one claude-plugin ComponentRef

`installed_plugins.json` indexes by `<plugin>@<marketplace>` keys and the
value is an array of install entries. The schema reserves capacity for
per-scope multiplexing (e.g., separate installs at user vs project scope).
In practice today entries are single-element. When multi-element, prefer
the entry whose `scope` field matches the settings layer that enabled the
plugin; fall back to `[0]` with a warning.

Settings layering is mode-specific:
- `fs` mode reads user + project + local (via `settings_layers.merged("fs")`).
- `repo` mode skips local (machine-local, not CI-relevant).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from tools.component_ref import ComponentRef
from tools.parsers.settings_layers import (
    SCOPE_PRECEDENCE,
    SettingsLayers,
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
    enabled_plugins = effective.get("enabledPlugins") or {}
    if not isinstance(enabled_plugins, dict):
        return refs, warnings

    lockfile_path = install_root / "plugins" / "installed_plugins.json"
    if not lockfile_path.exists():
        return refs, warnings

    try:
        lockfile = json.loads(lockfile_path.read_text())
    except json.JSONDecodeError as exc:
        warnings.append(f"installed_plugins.json malformed: {exc}")
        return refs, warnings

    plugins_map = lockfile.get("plugins") or {}
    if not isinstance(plugins_map, dict):
        return refs, warnings

    for plugin_key, is_enabled in enabled_plugins.items():
        if not is_enabled:
            continue
        entries = plugins_map.get(plugin_key)
        if not isinstance(entries, list) or not entries:
            warnings.append(
                f"plugin {plugin_key} enabled but missing from installed_plugins.json"
            )
            continue

        scope = _enabling_scope(plugin_key, layers)
        entry, index, warning = _select_install_entry(entries, scope)
        if warning is not None:
            warnings.append(f"{plugin_key}: {warning}")

        plugin_name, marketplace = _split_plugin_key(plugin_key)
        version = entry.get("version")
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

    return refs, warnings


def _split_plugin_key(plugin_key: str) -> tuple[str, Optional[str]]:
    if "@" in plugin_key:
        name, marketplace = plugin_key.split("@", 1)
        return name, marketplace
    return plugin_key, None


def _enabling_scope(plugin_key: str, layers: SettingsLayers) -> Optional[str]:
    """Return the highest-precedence scope where the plugin is set true.

    Used to break ties in `installed_plugins.json` arrays: when multiple
    entries exist, prefer the install whose `scope` field matches the scope
    that enabled it.
    """
    by_scope = layers.by_scope()
    for scope in SCOPE_PRECEDENCE:
        scope_data = by_scope.get(scope, {})
        enabled = scope_data.get("enabledPlugins", {})
        if isinstance(enabled, dict) and enabled.get(plugin_key) is True:
            return scope
    return None


def _select_install_entry(
    entries: list[dict], enabling_scope: Optional[str]
) -> tuple[dict, int, Optional[str]]:
    """Pick the install entry to emit a component for.

    Single-element list (the common case): take it.
    Multi-element list: prefer the entry whose `scope` matches the enabling
    scope; fall back to `[0]` with a warning string for the caller to surface.
    """
    if len(entries) == 1:
        return entries[0], 0, None
    if enabling_scope is not None:
        for index, entry in enumerate(entries):
            if entry.get("scope") == enabling_scope:
                return entry, index, None
    warning = (
        f"plugin has {len(entries)} installed entries with no scope match; taking [0]"
    )
    return entries[0], 0, warning
