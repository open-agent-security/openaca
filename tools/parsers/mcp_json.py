"""Parse mcp.json / .mcp.json files: extract MCP server installations.

There is no official `mcp.json` schema (see modelcontextprotocol#2219); each
host (Claude Code, Claude Desktop, Cursor, VS Code, Codex) defines its own
file conventions on top of the wire-protocol spec. We handle the dominant
JSON shapes here:

- `mcpServers` root (Claude Code, Claude Desktop, Cursor, plugin manifests).
- `servers` root (VS Code's `.vscode/mcp.json`).

V0 detection scope is package-pinned stdio servers (npx/uvx + binary
fallback) so ASVE can alias upstream CVE/GHSA records via PURL. URL/HTTP
transports, secret-surface fields (`env`/`headers`/`oauth`), and TOML
configs are out of V0 scope.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.component_ref import ComponentRef

NPM_PINNED_RE = re.compile(r"^(?P<name>(?:@[^/]+/)?[^@]+)@(?P<version>[^@\s]+)$")
PYPI_PINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[A-Za-z0-9_.+-]+)$")
PYPI_AT_VERSION_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)@(?P<version>[^@\s]+)$")
PYPI_UNPINNED_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)$")
INTERPOLATION_RE = re.compile(r"\$\{[^}]+\}")


def _has_interpolation(spec: str) -> bool:
    return bool(INTERPOLATION_RE.search(spec))


def _extract_flag_value(
    args: list[str], flag_long: str, flag_short: str | None = None
) -> str | None:
    """Return the value of `--<flag_long>=<v>`, `--<flag_long> <v>`, or
    `-<flag_short>=<v>` / `-<flag_short> <v>` if present.

    Stops at `--` (option terminator)."""
    long_eq = f"--{flag_long}="
    long_bare = f"--{flag_long}"
    short_eq = f"-{flag_short}=" if flag_short else None
    short_bare = f"-{flag_short}" if flag_short else None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            break
        if a.startswith(long_eq):
            return a[len(long_eq) :]
        if a == long_bare and i + 1 < len(args):
            return args[i + 1]
        if short_eq and a.startswith(short_eq):
            return a[len(short_eq) :]
        if short_bare and a == short_bare and i + 1 < len(args):
            return args[i + 1]
        i += 1
    return None


_FLAGS_WITH_VALUE = (
    "--package",
    "-p",
    "--from",
    "--with",
    "--python",
    # npx -c/--call takes a shell snippet; it's not a package spec and
    # the value must be excluded from positional analysis.
    "--call",
    "-c",
)


def _positional_args(args: list[str]) -> list[str]:
    """Return positional args, treating `--` as option terminator."""
    out: list[str] = []
    after_terminator = False
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if after_terminator:
            out.append(a)
            continue
        if a == "--":
            after_terminator = True
            continue
        if a.startswith("-"):
            # Flags consuming a separate value (handled in _extract_flag_value)
            # would otherwise leave that value here as a "positional" — skip it.
            if a in _FLAGS_WITH_VALUE:
                skip_next = True
            continue
        out.append(a)
    return out


def _classify_npm_spec(spec: str) -> tuple[str | None, str | None]:
    """Match a single npm spec like `@scope/name@1.2.3` or `bare-name`."""
    if _has_interpolation(spec):
        return None, None
    m = NPM_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version")
    return spec, None


def _classify_pypi_spec(spec: str) -> tuple[str | None, str | None, bool]:
    """Match a single PyPI spec: `name==1.2.3`, `name@1.2.3` (uvx form), or bare `name`."""
    if _has_interpolation(spec):
        return None, None, False
    m = PYPI_PINNED_RE.match(spec)
    if m:
        return m.group("name"), m.group("version"), True
    m = PYPI_AT_VERSION_RE.match(spec)
    if m:
        return m.group("name"), m.group("version"), True
    m = PYPI_UNPINNED_RE.match(spec)
    if m:
        return m.group("name"), None, False
    return None, None, False


def _parse_npx_args(args: list[str]) -> tuple[str | None, str | None]:
    inline = _extract_flag_value(args, "package", "p")
    if inline is not None:
        return _classify_npm_spec(inline)
    positional = _positional_args(args)
    if not positional:
        return None, None
    return _classify_npm_spec(positional[0])


def _parse_uvx_args(args: list[str]) -> tuple[str | None, str | None, bool]:
    inline = _extract_flag_value(args, "from")
    if inline is not None:
        return _classify_pypi_spec(inline)
    positional = _positional_args(args)
    if not positional:
        return None, None, False
    return _classify_pypi_spec(positional[0])


def _command_dispatch(command: str | None, args: list[str]) -> tuple[str, list[str]]:
    """Normalize `uv tool run <pkg>` into the equivalent `uvx <pkg>` form.

    Scans for an adjacent `tool run` pair anywhere in args, so global uv
    flags like `--offline` before the subcommand (e.g.,
    `uv --offline tool run weather-mcp==0.5.0`) still dispatch correctly.
    Returns (effective_command, effective_args).
    """
    if not command or _classify_command(command) != "uv":
        return command or "", args
    for i in range(len(args) - 1):
        if args[i] == "tool" and args[i + 1] == "run":
            return "uvx", args[i + 2 :]
    return command, args


def _classify_command(command: str) -> str:
    """Return the canonical command name for classification.

    Strips directory prefix and extension so `npx`, `/usr/local/bin/npx`,
    and `npx.cmd` all classify as `"npx"`. Case-sensitive (POSIX-correct):
    `/opt/NPX` stays `NPX` and falls to binary fallback.

    V0 is POSIX-only. Windows-specific edge cases (backslash paths,
    PATHEXT-resolved bare uppercase tokens) are deferred to V1 — they
    require disambiguating Windows-vs-POSIX intent from the manifest,
    which we can't do reliably without an OS hint.
    """
    return Path(command).stem


def parse_mcp_servers(
    servers: dict, source_manifest: str, locator_prefix: str = "$.mcpServers"
) -> list[ComponentRef]:
    """Convert an `mcpServers`/`servers` dict into ComponentRefs."""
    if not isinstance(servers, dict):
        return []
    refs: list[ComponentRef] = []
    for server_name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("disabled") is True:
            continue
        raw_command = entry.get("command")
        raw_args = entry.get("args") or []
        if not isinstance(raw_args, list):
            raw_args = []
        # Drop any non-string elements: `_extract_flag_value` and
        # `_positional_args` call `.startswith()` on each arg, so a stray
        # `null`, integer, or nested object would AttributeError mid-scan.
        raw_args = [a for a in raw_args if isinstance(a, str)]
        command, args = _command_dispatch(
            raw_command if isinstance(raw_command, str) else None,
            raw_args,
        )
        locator = f"{locator_prefix}.{server_name}"
        cmd_class = _classify_command(command) if command else ""
        if cmd_class == "npx":
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
        elif cmd_class == "uvx":
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
        elif command:
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
    if not isinstance(data, dict):
        return []
    # `mcpServers` (Claude Code/Desktop/Cursor) and `servers` (VS Code) are
    # two names for the same shape. Prefer mcpServers when both exist —
    # only relevant if a config file straddles host conventions.
    if isinstance(data.get("mcpServers"), dict):
        return parse_mcp_servers(
            data["mcpServers"],
            source_manifest=str(path),
            locator_prefix="$.mcpServers",
        )
    if isinstance(data.get("servers"), dict):
        return parse_mcp_servers(
            data["servers"],
            source_manifest=str(path),
            locator_prefix="$.servers",
        )
    return []
