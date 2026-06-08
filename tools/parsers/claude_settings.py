"""Parse .claude/settings.json — enumerate enabled Claude Code plugins.

`enabledPlugins` keys are `<plugin-name>@<marketplace>` (not `<plugin>@<version>`)
— settings doesn't carry version information. Refs are emitted with
`component_type="plugin"` + `name=<plugin-name>` + `version=None`, and the
logical identity includes marketplace when present:
`plugin/<marketplace>/<plugin-name>`.
Source ecosystem is unknown at settings scope; endpoint mode resolves versions
through `installed_plugins.json`.

Endpoint mode resolves versions through `installed_plugins.json` (see
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
        plugin_name, marketplace = _split_plugin_spec(plugin_spec)
        if not plugin_name:
            continue
        identity = _plugin_identity(plugin_name, marketplace)
        refs.append(
            ComponentRef(
                name=plugin_name,
                version=None,
                component_identity=identity,
                source_manifest=str(path),
                source_locator=f"$.enabledPlugins[{plugin_spec!r}]",
                extra={"component_type": "plugin", "marketplace": marketplace},
            )
        )
    return refs


def _split_plugin_spec(spec: str) -> tuple[str, str | None]:
    """Return `(name, marketplace)` from a settings plugin spec.

    Uses rsplit so scoped names like `@scope/plugin@market` parse correctly:
    `@scope/plugin@market` → name=`@scope/plugin`, marketplace=`market`.
    """
    if not isinstance(spec, str) or not spec:
        return "", None
    if "@" in spec:
        name, marketplace = spec.rsplit("@", 1)
        return name, marketplace or None
    return spec, None


def _plugin_identity(plugin_name: str, marketplace: str | None) -> str:
    if marketplace:
        return f"plugin/{marketplace}/{plugin_name}"
    return f"plugin/{plugin_name}"
