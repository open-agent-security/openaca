"""Codex's `config.toml` (plan 043 Task 1, spec: Codex Agent Kind).

One file carries four surfaces Codex's composition needs: `[mcp_servers.*]`
declares MCP servers inline, `[plugins."<name>@<marketplace>"]` records enable
state, `[marketplaces.*]` records where a plugin was resolved from, and
`[projects."<path>"]` records workspace trust.

`parse` emits only the MCP servers, because they are the only components this
file declares; plugins become refs during endpoint composition (where the cache
supplies the version and install path), and `[projects.*]` is posture. Callers
that need the other three read `load_config`.

**`enabled` is always stated, never omitted.** An absent key resolves to
enabled — verified against the audited endpoint, where `codex mcp list` reports
a server carrying no `enabled` key as `enabled` and one carrying
`enabled = false` as `disabled`. ADR-0055 requires the value be emitted rather
than inferred downstream, and requires a disabled component still be
inventoried: `enabled` records which, it does not gate membership.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers.mcp_json import parse_mcp_servers

# Codex's own key. `mcp_json` skips entries carrying Claude Code's
# `disabled: true`, which is the opposite polarity and a different key; Codex
# entries are never dropped for being disabled (ADR-0055).
_ENABLED_KEY = "enabled"


@dataclass(frozen=True)
class MarketplaceEntry:
    """One `[marketplaces.<name>]` table."""

    name: str
    source_type: str | None = None
    source: str | None = None
    last_revision: str | None = None


@dataclass(frozen=True)
class PluginEntry:
    """One `[plugins."<name>@<marketplace>"]` table."""

    name: str
    marketplace: str | None
    enabled: bool = True


@dataclass(frozen=True)
class ProjectEntry:
    """One `[projects."<path>"]` table."""

    path: str
    trust_level: str | None = None


@dataclass(frozen=True)
class CodexConfig:
    """The four surfaces `config.toml` carries.

    `plugins` is keyed `(marketplace, name)` rather than by the raw table key,
    so a caller matching a cached bundle against its enable state does not have
    to re-split the key and risk splitting it differently.
    """

    mcp_servers: dict = field(default_factory=dict)
    plugins: dict[tuple[str | None, str], PluginEntry] = field(default_factory=dict)
    marketplaces: dict[str, MarketplaceEntry] = field(default_factory=dict)
    projects: dict[str, ProjectEntry] = field(default_factory=dict)


def _as_bool(value: object, default: bool = True) -> bool:
    return value if isinstance(value, bool) else default


def _split_plugin_key(key: str) -> tuple[str | None, str]:
    """`"<name>@<marketplace>"` -> `(marketplace, name)`.

    Split on the **last** `@`: a plugin name may contain one, and the
    marketplace is the trailing field. A key with no `@` has no marketplace,
    which is a real state — a plugin not resolved from a registry gets no
    marketplace-qualified identity.
    """
    name, sep, marketplace = key.rpartition("@")
    if not sep:
        return None, key
    return marketplace, name


def _load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return data if isinstance(data, dict) else {}


def load_config(path: Path) -> CodexConfig:
    """Read `config.toml`. Malformed TOML raises `tomllib.TOMLDecodeError`."""
    data = _load_toml(path)

    raw_servers = data.get("mcp_servers")
    servers = raw_servers if isinstance(raw_servers, dict) else {}

    plugins: dict[tuple[str | None, str], PluginEntry] = {}
    raw_plugins = data.get("plugins")
    if isinstance(raw_plugins, dict):
        for key, entry in raw_plugins.items():
            if not isinstance(key, str):
                continue
            table = entry if isinstance(entry, dict) else {}
            marketplace, name = _split_plugin_key(key)
            plugins[(marketplace, name)] = PluginEntry(
                name=name,
                marketplace=marketplace,
                enabled=_as_bool(table.get(_ENABLED_KEY)),
            )

    marketplaces: dict[str, MarketplaceEntry] = {}
    raw_marketplaces = data.get("marketplaces")
    if isinstance(raw_marketplaces, dict):
        for name, entry in raw_marketplaces.items():
            table = entry if isinstance(entry, dict) else {}
            marketplaces[str(name)] = MarketplaceEntry(
                name=str(name),
                source_type=table.get("source_type"),
                source=table.get("source"),
                last_revision=table.get("last_revision"),
            )

    projects: dict[str, ProjectEntry] = {}
    raw_projects = data.get("projects")
    if isinstance(raw_projects, dict):
        for proj_path, entry in raw_projects.items():
            table = entry if isinstance(entry, dict) else {}
            projects[str(proj_path)] = ProjectEntry(
                path=str(proj_path),
                trust_level=table.get("trust_level"),
            )

    return CodexConfig(
        mcp_servers=servers,
        plugins=plugins,
        marketplaces=marketplaces,
        projects=projects,
    )


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    """MCP servers declared inline in `config.toml`.

    Reuses `mcp_json.parse_mcp_servers` rather than re-deriving launch specs:
    Codex's server tables carry the same `command`/`args`/`env`/`url` shape, so
    the ecosystem classification that drives ADR-0039 launch-dependency
    attachment must not be a second implementation that can disagree.
    """
    config = load_config(path)
    if not config.mcp_servers:
        return []

    refs = parse_mcp_servers(
        config.mcp_servers,
        source_manifest=str(path),
        locator_prefix="$.mcp_servers",
        strict=strict,
    )

    for ref in refs:
        extra = ref.extra or {}
        component_path = extra.get("component_path") or [{}]
        server_name = component_path[0].get("name")
        entry = config.mcp_servers.get(server_name)
        table = entry if isinstance(entry, dict) else {}
        extra[_ENABLED_KEY] = _as_bool(table.get(_ENABLED_KEY))

    return refs
