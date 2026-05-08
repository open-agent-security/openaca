"""Parse .claude/settings.json — enumerate enabled Claude Code plugins."""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    enabled = data.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return refs
    for plugin_spec, is_enabled in enabled.items():
        if not is_enabled:
            continue
        refs.append(
            ComponentRef(
                component_identity=f"claude-plugin/{plugin_spec}",
                source_manifest=str(path),
                source_locator=f"$.enabledPlugins[{plugin_spec!r}]",
            )
        )
    return refs
