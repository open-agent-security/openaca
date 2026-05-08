"""Parse .claude-plugin/plugin.json — plugin self-identity, deps, inlined MCP."""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers.mcp_json import parse_mcp_servers


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    refs: list[ComponentRef] = []

    name = data.get("name")
    version = data.get("version")
    if name:
        identity = f"claude-plugin/{name}"
        if version:
            identity = f"{identity}@{version}"
        refs.append(
            ComponentRef(
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

    servers = data.get("mcpServers") or {}
    if servers:
        refs.extend(
            parse_mcp_servers(
                servers,
                source_manifest=str(path),
                locator_prefix="$.mcpServers (inlined)",
            )
        )

    return refs
