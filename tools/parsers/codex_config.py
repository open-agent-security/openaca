"""Codex's `config.toml` (plan 043 Task 1, spec: Codex Agent Kind).

One file carries five surfaces Codex's composition needs: `[mcp_servers.*]`
declares MCP servers inline, `[plugins."<name>@<marketplace>"]` records enable
state, `[marketplaces.*]` records where a plugin was resolved from,
`[projects."<path>"]` records workspace trust, and an inline `[hooks]` table
(`[[hooks.<Event>]]`) is Codex's TOML rendering of the same envelope
`hooks_json` already reads from `hooks.json` — a documented alternative to the
sidecar file, scoped per config layer (verified against
developers.openai.com/codex/hooks: "Codex discovers hooks next to active
config layers in either of these forms: `hooks.json` [or] inline `[hooks]`
tables inside `config.toml`").

`parse` emits only the MCP servers, because they are the only components this
file declares; plugins become refs during endpoint composition (where the cache
supplies the version and install path), `[projects.*]` is posture, and
`[hooks]` is read by the caller (`graph_build._seed_codex_hooks`) through
`hooks_json.parse_settings_hooks` rather than duplicated here. Callers that
need any of the other four read `load_config`.

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
class AgentRoleEntry:
    """One `[agents."<role>"]` table — a subagent declared by the config.

    `config_file` points at the role's own TOML file and may sit anywhere:
    "Path to a TOML config layer for that role; relative paths resolve from the
    config file that declares the role."
    (developers.openai.com/codex/config-reference). The role name is the table
    key, not the referenced file's `name`, because the key is what selects the
    role.
    """

    name: str
    config_file: str | None = None
    description: str | None = None


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

    # Top-level surfaces that are PRESENT but of the wrong TOML type. Absent
    # and malformed are different states, and collapsing them is what let a
    # `mcp_servers = "bad"` or `hooks = []` silently drop a whole component
    # surface while coverage still read `complete`. Callers gap on these.
    malformed: tuple[str, ...] = ()

    mcp_servers: dict = field(default_factory=dict)
    plugins: dict[tuple[str | None, str], PluginEntry] = field(default_factory=dict)
    marketplaces: dict[str, MarketplaceEntry] = field(default_factory=dict)
    projects: dict[str, ProjectEntry] = field(default_factory=dict)
    agents: dict[str, AgentRoleEntry] = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)


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


# Every top-level table `load_config` reads. Named so a surface added later
# without malformed-handling fails the parametrised test rather than shipping
# the coercion bug again.
TOP_LEVEL_SURFACES: tuple[str, ...] = (
    "mcp_servers",
    "plugins",
    "marketplaces",
    "projects",
    "hooks",
    "agents",
)


def _table(data: dict, key: str, malformed: list[str]) -> dict:
    """A top-level table, distinguishing absent from present-but-malformed.

    `data.get(key) if isinstance(..., dict) else {}` — the pattern this
    replaces — reports a present, wrongly-typed surface as an empty one. Every
    downstream reader then takes its "nothing here" path: no components, no
    warning, and `composition_coverage` still `complete`. Recording the key
    instead lets the caller raise or gap.
    """
    raw = data.get(key)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        malformed.append(key)
        return {}
    return raw


def load_config(path: Path) -> CodexConfig:
    """Read `config.toml`. Malformed TOML raises `tomllib.TOMLDecodeError`."""
    data = _load_toml(path)

    malformed: list[str] = []
    servers = _table(data, "mcp_servers", malformed)

    plugins: dict[tuple[str | None, str], PluginEntry] = {}
    raw_plugins = _table(data, "plugins", malformed)
    if raw_plugins:
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
    raw_marketplaces = _table(data, "marketplaces", malformed)
    if raw_marketplaces:
        for name, entry in raw_marketplaces.items():
            table = entry if isinstance(entry, dict) else {}
            marketplaces[str(name)] = MarketplaceEntry(
                name=str(name),
                source_type=table.get("source_type"),
                source=table.get("source"),
                last_revision=table.get("last_revision"),
            )

    projects: dict[str, ProjectEntry] = {}
    raw_projects = _table(data, "projects", malformed)
    if raw_projects:
        for proj_path, entry in raw_projects.items():
            table = entry if isinstance(entry, dict) else {}
            projects[str(proj_path)] = ProjectEntry(
                path=str(proj_path),
                trust_level=table.get("trust_level"),
            )

    # Malformed input (e.g. `hooks = "bad"`) is preserved rather than coerced to
    # `{}`: both callers (`graph_build._add_codex_declared_config_hooks`,
    # `_seed_codex_hooks_from_layer`) already gate on `if not config.hooks`
    # before handing this value to `hooks_json.parse_settings_hooks(...,
    # strict=True)`, which raises on a non-dict shape so the caller's
    # `record_gap` sees it. Coercing here (as `mcp_servers` used to, before
    # `parse()` was changed to read the raw TOML directly) would make that
    # `strict=True` unreachable for a malformed `hooks` table.
    agents: dict[str, AgentRoleEntry] = {}
    raw_agents = _table(data, "agents", malformed)
    if raw_agents:
        for role, entry in raw_agents.items():
            table = entry if isinstance(entry, dict) else {}
            config_file = table.get("config_file")
            description = table.get("description")
            agents[str(role)] = AgentRoleEntry(
                name=str(role),
                config_file=config_file if isinstance(config_file, str) else None,
                description=description if isinstance(description, str) else None,
            )

    # `hooks` goes through the same helper as every other surface. It used to
    # be preserved raw so a downstream `strict=True` parse would reject it, but
    # that only worked for TRUTHY malformed values — `hooks = []` passed the
    # callers' `if not config.hooks` gate and was never validated at all.
    hooks = _table(data, "hooks", malformed)

    return CodexConfig(
        malformed=tuple(malformed),
        mcp_servers=servers,
        plugins=plugins,
        marketplaces=marketplaces,
        projects=projects,
        agents=agents,
        hooks=hooks,
    )


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    """MCP servers declared inline in `config.toml`.

    Reuses `mcp_json.parse_mcp_servers` rather than re-deriving launch specs:
    Codex's server tables carry the same `command`/`args`/`env`/`url` shape, so
    the ecosystem classification that drives ADR-0039 launch-dependency
    attachment must not be a second implementation that can disagree.

    Reads the raw TOML directly rather than going through `load_config`:
    `load_config`'s `mcp_servers` field coerces a malformed top-level value
    (e.g. `mcp_servers = "bad"`) to `{}` for every caller, including the ones
    that only want plugins/marketplaces/projects and don't care. That coercion
    ran before `strict` ever got a look, so a malformed table was silently
    treated as "no servers declared" instead of rejected — the same shape
    `mcp_json.parse` checks at its own top level for `mcpServers`/`servers`.
    """
    data = _load_toml(path)
    raw_servers = data.get("mcp_servers")
    if raw_servers is None:
        return []
    if not isinstance(raw_servers, dict):
        if strict:
            raise ValueError("mcp_servers must be an object")
        return []

    refs = parse_mcp_servers(
        raw_servers,
        source_manifest=str(path),
        locator_prefix="$.mcp_servers",
        strict=strict,
    )

    for ref in refs:
        extra = ref.extra or {}
        component_path = extra.get("component_path") or [{}]
        server_name = component_path[0].get("name")
        entry = raw_servers.get(server_name)
        table = entry if isinstance(entry, dict) else {}
        extra[_ENABLED_KEY] = _as_bool(table.get(_ENABLED_KEY))

    return refs
