"""Parse .claude-plugin/plugin.json — plugin self-identity and bundled surfaces.

Two entry points:

- `parse(path)` — repo-mode: a single `plugin.json` read in isolation, with
  relative paths in `mcpServers` resolved from the plugin root
  (`path.parent.parent`). Emits plugin self-identity, dependencies, MCPs,
  and bundled skills/hooks/commands/agents.
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
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers.claude_plugin_root import walk_plugin_root


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    if not isinstance(data, dict):
        return refs

    raw_name = data.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else None
    version = data.get("version")
    if not isinstance(version, (str, type(None))):
        version = None
    attributed_to = None
    if name:
        component_identity = f"plugin/{name}"
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

    refs.extend(
        walk_plugin_root(
            path.parent.parent,
            plugin_name=name or "",
            plugin_data=data,
            plugin_json_path=path,
            attributed_to=attributed_to,
        )
    )
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
    raw_name = data.get("name")
    plugin_name = raw_name if isinstance(raw_name, str) else ""
    return walk_plugin_root(
        install_root,
        plugin_name=plugin_name,
        plugin_data=data,
        plugin_json_path=plugin_json,
        attributed_to=attributed_to,
    )
