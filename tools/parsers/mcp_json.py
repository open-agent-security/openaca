"""Parse mcp.json / .mcp.json files: extract MCP server installations."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.component_ref import ComponentRef

NPM_PINNED_RE = re.compile(r"^(?P<name>(?:@[^/]+/)?[^@]+)@(?P<version>[^@\s]+)$")
PYPI_PINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+-]+)$")
PYPI_UNPINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)$")


def _parse_npx_args(args: list[str]) -> tuple[str | None, str | None]:
    real_args = [a for a in args if not a.startswith("-")]
    if not real_args:
        return None, None
    spec = real_args[0]
    m = NPM_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version")
    return spec, None


def _parse_uvx_args(args: list[str]) -> tuple[str | None, str | None, bool]:
    real_args = [a for a in args if not a.startswith("-")]
    if not real_args:
        return None, None, False
    spec = real_args[0]
    m = PYPI_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version"), True
    m = PYPI_UNPINNED_RE.match(spec)
    if m:
        return m.group("name"), None, False
    return None, None, False


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    servers = data.get("mcpServers") or {}
    refs: list[ComponentRef] = []
    for server_name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        args = entry.get("args") or []
        locator = f"$.mcpServers.{server_name}"
        if command == "npx":
            name, version = _parse_npx_args(args)
            if name and version:
                refs.append(
                    ComponentRef(
                        ecosystem="npm",
                        name=name,
                        version=version,
                        source_manifest=str(path),
                        source_locator=locator,
                    )
                )
            elif name:
                refs.append(
                    ComponentRef(
                        component_identity=f"mcp-stdio/npx-unpinned:{name}",
                        source_manifest=str(path),
                        source_locator=locator,
                    )
                )
        elif command == "uvx":
            name, version, pinned = _parse_uvx_args(args)
            if name and pinned:
                refs.append(
                    ComponentRef(
                        ecosystem="PyPI",
                        name=name,
                        version=version,
                        source_manifest=str(path),
                        source_locator=locator,
                    )
                )
            elif name:
                refs.append(
                    ComponentRef(
                        component_identity=f"mcp-stdio/uvx-unpinned:{name}",
                        source_manifest=str(path),
                        source_locator=locator,
                    )
                )
        else:
            refs.append(
                ComponentRef(
                    component_identity=f"mcp-stdio/binary:{command}",
                    source_manifest=str(path),
                    source_locator=locator,
                )
            )
    return refs
