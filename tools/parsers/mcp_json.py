"""Parse mcp.json / .mcp.json files: extract MCP server installations."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.component_ref import ComponentRef

NPM_PINNED_RE = re.compile(r"^(?P<name>(?:@[^/]+/)?[^@]+)@(?P<version>[^@\s]+)$")
PYPI_PINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+-]+)$")
PYPI_UNPINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)$")


def _extract_inline_pkg_spec(args: list[str], flag: str) -> str | None:
    """Return the value of `--<flag>=<value>` if present, else None.

    Handles `npx --package=<spec>` and `uvx --from=<spec>` forms; both nominate
    the launched package out-of-band from positional args.
    """
    prefix = f"--{flag}="
    for a in args:
        if a.startswith(prefix):
            return a[len(prefix) :]
    return None


def _classify_npm_spec(spec: str) -> tuple[str | None, str | None]:
    """Match a single npm spec like `@scope/name@1.2.3` or `bare-name`."""
    m = NPM_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version")
    return spec, None


def _classify_pypi_spec(spec: str) -> tuple[str | None, str | None, bool]:
    """Match a single PyPI spec like `name==1.2.3` or `name`."""
    m = PYPI_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version"), True
    m = PYPI_UNPINNED_RE.match(spec)
    if m:
        return m.group("name"), None, False
    return None, None, False


def _parse_npx_args(args: list[str]) -> tuple[str | None, str | None]:
    inline = _extract_inline_pkg_spec(args, "package")
    if inline is not None:
        return _classify_npm_spec(inline)
    real_args = [a for a in args if not a.startswith("-")]
    if not real_args:
        return None, None
    return _classify_npm_spec(real_args[0])


def _parse_uvx_args(args: list[str]) -> tuple[str | None, str | None, bool]:
    inline = _extract_inline_pkg_spec(args, "from")
    if inline is not None:
        return _classify_pypi_spec(inline)
    real_args = [a for a in args if not a.startswith("-")]
    if not real_args:
        return None, None, False
    return _classify_pypi_spec(real_args[0])


def parse_mcp_servers(
    servers: dict, source_manifest: str, locator_prefix: str = "$.mcpServers"
) -> list[ComponentRef]:
    """Convert an `mcpServers` dict into ComponentRefs. Reused by claude_plugin."""
    if not isinstance(servers, dict):
        return []
    refs: list[ComponentRef] = []
    for server_name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        args = entry.get("args") or []
        locator = f"{locator_prefix}.{server_name}"
        if command == "npx":
            name, version = _parse_npx_args(args)
            if name and version:
                refs.append(
                    ComponentRef(
                        ecosystem="npm",
                        name=name,
                        version=version,
                        source_manifest=source_manifest,
                        source_locator=locator,
                    )
                )
            elif name:
                refs.append(
                    ComponentRef(
                        component_identity=f"mcp-stdio/npx-unpinned:{name}",
                        source_manifest=source_manifest,
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
                        source_manifest=source_manifest,
                        source_locator=locator,
                    )
                )
            elif name:
                refs.append(
                    ComponentRef(
                        component_identity=f"mcp-stdio/uvx-unpinned:{name}",
                        source_manifest=source_manifest,
                        source_locator=locator,
                    )
                )
        elif isinstance(command, str) and command:
            refs.append(
                ComponentRef(
                    component_identity=f"mcp-stdio/binary:{command}",
                    source_manifest=source_manifest,
                    source_locator=locator,
                )
            )
    return refs


def parse(path: Path) -> list[ComponentRef]:
    data = json.loads(path.read_text())
    servers = data.get("mcpServers") or {}
    return parse_mcp_servers(servers, source_manifest=str(path))
