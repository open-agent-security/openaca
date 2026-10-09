"""Devin CLI's own configuration files (ADR-0070).

`config.json`, `config.local.json`, `mcp_config.json`, `mcp_config.local.json`
and `hooks.v1.json` are JSON with comments, at three levels: user
(`<config root>/`), project (`.devin/`) and project-local (`.devin/*.local.json`).
Devin reads the files it imports from other runtimes with the same loader, so
they are read that way here too.

Two facts about Devin, not about the files' owners, live here:

- **`read_config_from`** switches each import on or off per tool. A switch set
  at a higher-precedence level wins; `null` reads as `true`, and an unset
  switch defaults to `true` (docs: Configuration Import).
- **Devin's `ServerConfig` accepts aliases** the shared MCP parser does not:
  `serverUrl` for `url` (the audited binary validates that "url and serverUrl
  must not disagree") and `transport` for `type`. They are mapped here, for the
  files Devin reads only, so no other kind starts accepting them.
"""

from __future__ import annotations

from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers import hooks_json, jsonc, mcp_json

READ_CONFIG_FROM = "read_config_from"

# The `read_config_from` keys whose imports this kind composes. OpenCode and
# Zed are deferred (docs/specs/devin-cli-agent-kind.md "Out of the first pass"),
# and `agents_standard` gates instruction files only.
IMPORT_CLAUDE = "claude"
IMPORT_CURSOR = "cursor"
IMPORT_WINDSURF = "windsurf"
IMPORT_COPILOT = "copilot"

PROJECT_CONFIG_DIR = ".devin"
CONFIG_FILENAME = "config.json"
LOCAL_CONFIG_FILENAME = "config.local.json"
MCP_CONFIG_FILENAME = "mcp_config.json"
LOCAL_MCP_CONFIG_FILENAME = "mcp_config.local.json"
HOOKS_FILENAME = "hooks.v1.json"


def load(path: Path) -> dict:
    """Read one Devin-read JSON-with-comments file. Raises `OSError` or
    `ValueError` (including for a non-object root) so a caller decides
    whether the failure is a coverage gap."""
    data = jsonc.load_path(path)
    if not isinstance(data, dict):
        raise ValueError("configuration must contain an object")
    return data


def import_switch(config: dict, tool: str) -> bool | None:
    """`read_config_from.<tool>` as Devin reads it: `False` disables, `True`
    or `null` enables, and absence (or any other value) says nothing."""
    switches = config.get(READ_CONFIG_FROM)
    if not isinstance(switches, dict) or tool not in switches:
        return None
    value = switches[tool]
    if value is False:
        return False
    if value is True or value is None:
        return True
    return None


class ImportSwitches:
    """Resolve whether an import is on for a directory, nearest level first.

    For a directory `d`, the levels searched are `d/.devin/config.local.json`,
    `d/.devin/config.json`, then the same pair in each ancestor up to and
    including `stop_at`, then `user_config`. The first explicit switch wins;
    none means `True`. Nested project configs take precedence over ancestor
    ones (docs: Configuration Precedence), so a deeper directory is searched
    first.

    A file that does not parse declares no switch here; the composition pass
    reading the same file records it as a coverage gap.
    """

    def __init__(self, *, stop_at: Path | None, user_config: Path | None = None) -> None:
        self._stop_at = stop_at.resolve() if stop_at is not None else None
        self._user_config = user_config
        self._configs: dict[Path, dict] = {}

    def enabled(self, directory: Path | None, tool: str) -> bool:
        for config_path in self._chain(directory):
            value = import_switch(self._config(config_path), tool)
            if value is not None:
                return value
        return True

    def _chain(self, directory: Path | None) -> list[Path]:
        chain: list[Path] = []
        if directory is not None and self._stop_at is not None:
            current = directory.resolve()
            while True:
                config_dir = current / PROJECT_CONFIG_DIR
                chain += [config_dir / LOCAL_CONFIG_FILENAME, config_dir / CONFIG_FILENAME]
                if current == self._stop_at or current.parent == current:
                    break
                if not current.is_relative_to(self._stop_at):
                    break
                current = current.parent
        if self._user_config is not None:
            chain.append(self._user_config)
        return chain

    def _config(self, path: Path) -> dict:
        if path not in self._configs:
            try:
                self._configs[path] = load(path) if path.is_file() else {}
            except (OSError, ValueError):
                self._configs[path] = {}
        return self._configs[path]


def server_map(data: dict, *, allow_flat: bool) -> tuple[dict, str] | None:
    """The MCP server map a file declares, with its locator prefix.

    `mcpServers` wherever it appears. A flat `{name: {command|url}}` root is
    accepted only where the owner's format allows one (`allow_flat`), as
    `mcp_json.parse` does for Claude Code's `.mcp.json`. Raises `ValueError`
    for a non-object `mcpServers`; `None` means the file declares no servers.
    """
    if "mcpServers" in data:
        servers = data["mcpServers"]
        if not isinstance(servers, dict):
            raise ValueError("mcpServers must be an object")
        return servers, "$.mcpServers"
    if (
        allow_flat
        and data
        and all(
            isinstance(v, dict) and ("command" in v or "url" in v or "serverUrl" in v)
            for v in data.values()
        )
    ):
        return data, "$"
    return None


def normalize_server_entry(entry: object) -> object:
    """Map Devin's `ServerConfig` aliases onto the shared parser's names."""
    if not isinstance(entry, dict):
        return entry
    out = dict(entry)
    if "url" not in out and isinstance(out.get("serverUrl"), str):
        out["url"] = out["serverUrl"]
    if "type" not in out and isinstance(out.get("transport"), str):
        out["type"] = out["transport"]
    return out


def parse_server(
    name: str, entry: object, *, source_manifest: str, locator_prefix: str
) -> list[ComponentRef]:
    """One named server, strictly. A disabled server yields nothing, which is
    also how a higher level's `"disabled": true` shadows a lower definition."""
    return mcp_json.parse_mcp_servers(
        {name: normalize_server_entry(entry)},
        source_manifest=source_manifest,
        locator_prefix=locator_prefix,
        strict=True,
    )


def parse_mcp_file(path: Path, *, allow_flat: bool = False) -> list[ComponentRef]:
    """Every server a Devin-read MCP file declares, strictly (registry use)."""
    found = server_map(load(path), allow_flat=allow_flat)
    if found is None:
        return []
    servers, prefix = found
    refs: list[ComponentRef] = []
    for name, entry in servers.items():
        if not isinstance(name, str):
            raise ValueError("MCP server names must be strings")
        refs.extend(parse_server(name, entry, source_manifest=str(path), locator_prefix=prefix))
    return refs


def parse_settings_file(path: Path, *, scope: str = "project") -> list[ComponentRef]:
    """The components a Devin-read settings-shaped file declares, strictly:
    its `mcpServers` and its `hooks` block (registry use)."""
    data = load(path)
    refs: list[ComponentRef] = []
    found = server_map(data, allow_flat=False)
    if found is not None:
        servers, prefix = found
        for name, entry in servers.items():
            if not isinstance(name, str):
                raise ValueError("MCP server names must be strings")
            refs.extend(parse_server(name, entry, source_manifest=str(path), locator_prefix=prefix))
    if "hooks" in data:
        refs.extend(hooks_json.parse_settings_hooks(path, data["hooks"], scope=scope, strict=True))
    return refs
