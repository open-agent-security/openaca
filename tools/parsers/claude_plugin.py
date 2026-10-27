"""Parse `.claude-plugin/plugin.json` for plugin identity and bundled surfaces.

`parse(path)` reads one `plugin.json` in isolation. Relative paths in
`mcpServers` resolve from the plugin root (`path.parent.parent`). It emits
the plugin self-identity, dependencies, MCPs, and bundled skills, hooks,
commands, and agents. Endpoint scans call `walk_plugin_root` directly.
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
    if name:
        component_identity = f"plugin/{name}"
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
        )
    )
    return refs
