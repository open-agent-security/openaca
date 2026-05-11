"""Parse .claude/settings.json — enumerate enabled Claude Code plugins.

`enabledPlugins` keys are `<plugin-name>@<marketplace>` (not `<plugin>@<version>`)
— settings doesn't carry version information. Refs are emitted with
`ecosystem="claude-plugin"` + `name=<plugin-name>` + `version=None` so the
matcher's `_match_versioned` path can fire against `claude-plugin` advisories
(ADR-0006). With no version, matches are emitted at "low" confidence with a
"pin to verify" reason — accurate for unversioned-declaration matching.

fs mode resolves versions through `installed_plugins.json` (see
`claude_install.py`). Repo mode has no such lockfile, so settings declarations
stay version-less here.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    if not isinstance(data, dict):
        return refs
    enabled = data.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return refs
    for plugin_spec, is_enabled in enabled.items():
        if is_enabled is not True:
            continue
        plugin_name = _split_plugin_name(plugin_spec)
        if not plugin_name:
            continue
        identity = f"claude-plugin/{plugin_name}"
        refs.append(
            ComponentRef(
                ecosystem="claude-plugin",
                name=plugin_name,
                version=None,
                component_identity=identity,
                source_manifest=str(path),
                source_locator=f"$.enabledPlugins[{plugin_spec!r}]",
            )
        )
    return refs


def _split_plugin_name(spec: str) -> str:
    """Strip the `@<marketplace>` suffix from a plugin spec; return name only.

    Uses rsplit so scoped names like `@scope/plugin@market` parse correctly:
    `@scope/plugin@market` → name=`@scope/plugin`.
    """
    if not isinstance(spec, str) or not spec:
        return ""
    if "@" in spec:
        name, _ = spec.rsplit("@", 1)
        return name
    return spec
