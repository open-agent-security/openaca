"""Parse .claude-plugin/plugin.json — plugin self-identity, deps, inlined MCP."""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers import mcp_json
from tools.parsers.mcp_json import parse_mcp_servers


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []
    if not isinstance(data, dict):
        return refs

    name = data.get("name")
    version = data.get("version")
    if name:
        identity = f"claude-plugin/{name}"
        if version:
            identity = f"{identity}@{version}"
        # Tag with ecosystem="claude-plugin" so the matcher's _match_versioned
        # path fires on plugin advisories. component_identity stays for
        # backwards-compatible identity reporting.
        refs.append(
            ComponentRef(
                ecosystem="claude-plugin",
                name=name,
                version=version,
                component_identity=identity,
                source_manifest=str(path),
                source_locator="$",
            )
        )

    deps = data.get("dependencies")
    if not isinstance(deps, list):
        deps = []
    for i, dep in enumerate(deps):
        locator = f"$.dependencies[{i}]"
        if isinstance(dep, str):
            refs.append(
                ComponentRef(
                    component_identity=f"claude-plugin-dep/{dep}",
                    source_manifest=str(path),
                    source_locator=locator,
                )
            )
        elif isinstance(dep, dict) and dep.get("name"):
            ident = f"claude-plugin-dep/{dep['name']}"
            if dep.get("version"):
                ident = f"{ident}@{dep['version']}"
            refs.append(
                ComponentRef(
                    component_identity=ident,
                    source_manifest=str(path),
                    source_locator=locator,
                )
            )

    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        refs.extend(
            parse_mcp_servers(
                servers,
                source_manifest=str(path),
                locator_prefix="$.mcpServers (inlined)",
            )
        )
    elif isinstance(servers, str):
        # plugin.json lives at <plugin-root>/.claude-plugin/plugin.json.
        # Relative paths in plugin.json resolve from plugin root (CLAUDE_PLUGIN_ROOT
        # semantics), not from the manifest's parent directory.
        plugin_root = path.parent.parent
        plugin_root_resolved = plugin_root.resolve()
        referenced = (plugin_root / servers).resolve()
        # Reject absolute paths and ../ traversal that escape the plugin root.
        if referenced.is_relative_to(plugin_root_resolved) and referenced.exists():
            try:
                refs.extend(mcp_json.parse(referenced))
            except Exception:
                # Malformed referenced .mcp.json should not abort plugin parsing.
                pass

    return refs
