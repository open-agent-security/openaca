"""Repo-mode surface descriptors (ADR-0053).

`graph_build`'s repo-mode helpers are structurally kind-neutral; they are
Claude-specific only in the directory/filename literals they match against
(`.claude`, `.claude-plugin`, `.mcp.json`, and two lookup tables). This module
names those literals as frozen, importable data so a second kind supplies its
own `RepoSurface` instead of forking the walker.

Imports only parser leaf modules — never `tools.graph_build` (which imports
this module) and never `tools.agent_kinds` (ADR-0044's one-way dependency).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tools.parsers import agent_plugins, claude_plugin
from tools.parsers.claude_command_agent import Kind


@dataclass(frozen=True)
class PluginFormat:
    """One candidate plugin-manifest shape a `RepoSurface` recognizes.

    `manifest_dir`/`manifest_filename` locate the candidate file; `detect`
    tests whether its *parsed content* qualifies as this format — the one
    field ADR-0053 permits to be a `Callable`, because qualification (e.g. an
    Agent Plugins manifest's `$schema`) is a content fact, not a path shape.

    `parse` is the second permitted `Callable`, and it is the same kind of
    fact: which parser reads this manifest shape is a property of the format,
    not of whichever caller happens to hold it. Carrying it here is what lets
    `realized_plugin_roots` ask "does this candidate realize?" for ANY kind's
    formats instead of branching on a specific format object.
    """

    manifest_dir: str
    manifest_filename: str
    detect: Callable[[dict], bool]
    parse: Callable[[Path], list] | None = None


@dataclass(frozen=True)
class BundledLayout:
    """Names of the surfaces bundled under a realized plugin root.

    `mcp_filenames` is an ORDERED tuple, not a single name: Cursor's folder
    discovery accepts root `.mcp.json` *or* `mcp.json`
    (docs/specs/cursor-agent-kind.md:283), so a single `str` field would
    force a false choice between the two. Each name is tried in order; the
    first that resolves to a file wins. Claude Code's tuple has one element
    (`(".mcp.json",)`), so this is behavior-preserving there.
    """

    skills_dir: str
    mcp_filenames: tuple[str, ...]
    hooks_filename: str
    commands_dir: str
    agents_dir: str


@dataclass(frozen=True)
class RepoSurface:
    """Names and shadowing rules a kind reads out of a repository tree.

    `config_dir` is the per-project config directory (`.claude`) under which
    project skills, settings, and standalone commands/agents live.
    `plugin_formats` is the ordered candidate list a plugin root's manifest is
    matched against; the first qualifying candidate wins.

    The remaining fields exist for a kind (Cursor) whose repo-mode surface
    isn't expressible through the single-`config_dir` fields above — multiple
    skill roots, no settings-equivalent surface, a project-scoped (not
    any-name) MCP manifest. They default to the empty/`None` values that keep
    `CLAUDE_CODE_SURFACE` behaviorally untouched; a kind that needs them reads
    them directly rather than through the `config_dir`-shaped helpers.

    - `skill_config_dirs`: config-dir roots searched for `<dir>/<project_skills_subdir>`
      at any depth, each walked *recursively* beneath its `skills` dir. Empty
      for Claude Code, which uses `config_dir` + the one-level project-skill
      walk instead.
    - `excluded_skill_dirs`: directory basenames that are never a skill root
      even when they sit beside `project_skills_subdir` (Cursor's
      vendor-owned `skills-cursor`).
    - `scoped_mcp_rels`: project-scoped MCP manifest relative paths
      (`.cursor/mcp.json`), matched at any depth, as opposed to
      `standalone_mcp_filenames`' any-name-anywhere matching.
    - `settings_rel`: the settings-equivalent manifest's repo-relative path,
      or `None` when the kind has no such surface at this layer.
    """

    config_dir: str
    plugin_formats: tuple[PluginFormat, ...]
    bundled: BundledLayout
    # `None` when `<config_dir>/<settings_filename>` isn't a JSON file
    # `claude_settings.parse` can read — e.g. Codex's is TOML, read by
    # `codex_config` instead. `_add_repo_standalone_components` skips the
    # settings branch entirely rather than feeding it a non-JSON file.
    settings_filename: str | None
    project_skills_subdir: str
    standalone_mcp_filenames: tuple[str, ...]
    command_agent_surfaces: tuple[tuple[str, Kind], ...]
    skill_config_dirs: tuple[str, ...] = ()
    excluded_skill_dirs: tuple[str, ...] = ()
    scoped_mcp_rels: tuple[str, ...] = ()
    settings_rel: str | None = None
    # A realized plugin root with no manifest at all is legitimate for this
    # kind. Cursor's marketplace cache ships such bundles and they load
    # entirely by folder discovery (docs/specs/cursor-agent-kind.md, Plugins),
    # so reporting them as unparseable is wrong — and policy mode escalates
    # that report to a hard error. Claude Code's plugins are named by an
    # install lockfile that points at a manifest, so a missing one there is a
    # real defect worth reporting.
    manifest_optional: bool = False
    # Content beneath a realized plugin root belongs to that plugin, not to
    # the tree: composition reaches it through the plugin branch, so any
    # OTHER consumer that walks the tree — evidence detection, registry
    # parse accounting — must not claim it independently. A nested fixture
    # (`examples/demo/plugin.json`, `examples/.cursor/mcp.json`) would
    # otherwise declare a phantom agent and inflate `source_unit_count`.
    #
    # The rule is kind-neutral, but activation is per-kind and deliberately
    # so. Claude Code has the same latent overreach (its
    # `standalone_mcp_filenames` match at any depth, so a `.claude-plugin`
    # bundle's own fixture `mcp.json` counts twice), and fixing it here would
    # change Claude Code's counts inside a Cursor change — destroying the
    # uncontaminated regression gate ADR-0053 built this parameterization to
    # preserve. Flipping this to `True` for Claude Code is a one-line change
    # that belongs in its own diff, with its own before/after count
    # assertions.
    excludes_plugin_owned_content: bool = False


def _detect_claude_plugin_manifest(data: dict) -> bool:
    return isinstance(data, dict)


def _detect_named_plugin_manifest(data: dict) -> bool:
    return isinstance(data.get("name"), str)


# §5.2: the Agent Plugins manifest schema URLs, rebuilt from the parser's
# public `SUPPORTED_SCHEMA_VERSIONS` (never the module's private URL map) so
# this stays in lockstep with `tools/parsers/agent_plugins.py` without a
# cross-module private import.
_AGENT_PLUGINS_SCHEMA_URLS = frozenset(
    f"https://agent-plugins.org/schemas/{version}/plugin.schema.json"
    for version in agent_plugins.SUPPORTED_SCHEMA_VERSIONS
)


def _detect_agent_plugins_manifest(data: dict) -> bool:
    schema = data.get("$schema")
    return isinstance(schema, str) and schema in _AGENT_PLUGINS_SCHEMA_URLS


CLAUDE_CODE_SURFACE = RepoSurface(
    config_dir=".claude",
    plugin_formats=(
        PluginFormat(
            manifest_dir=".claude-plugin",
            manifest_filename="plugin.json",
            detect=_detect_claude_plugin_manifest,
            parse=claude_plugin.parse,
        ),
    ),
    bundled=BundledLayout(
        skills_dir="skills",
        mcp_filenames=(".mcp.json",),
        hooks_filename="hooks/hooks.json",
        commands_dir="commands",
        agents_dir="agents",
    ),
    settings_filename="settings.json",
    project_skills_subdir="skills",
    # Standalone MCP manifest filenames discovered at any depth in repo mode
    # (parity with the REGISTRY `mcp.json` / `.mcp.json` / `claude_desktop_config.json`
    # patterns, which match by bare name anywhere in the tree).
    standalone_mcp_filenames=("mcp.json", ".mcp.json", "claude_desktop_config.json"),
    # `.claude/<subdir>/**/*.md` agent-component surfaces discovered at any depth in
    # repo mode, mirroring the REGISTRY command/agent patterns.
    command_agent_surfaces=(("commands", "command"), ("agents", "agent")),
)

# Cursor's own plugin format, `.cursor-plugin/plugin.json`.
_CURSOR_PLUGIN_FORMAT = PluginFormat(
    manifest_dir=".cursor-plugin",
    manifest_filename="plugin.json",
    detect=_detect_named_plugin_manifest,
    parse=claude_plugin.parse,
)

# The portable Agent Plugins format, `plugin.json` at the candidate root
# itself — `manifest_dir=""` is the one shape `_resolve_plugin_format`/
# `_find_plugin_roots` in `tools/graph_build.py` special-case: no subdirectory
# level, the manifest sits directly in the plugin root.
_AGENT_PLUGINS_FORMAT = PluginFormat(
    manifest_dir="",
    manifest_filename="plugin.json",
    detect=_detect_agent_plugins_manifest,
    parse=agent_plugins.parse,
)

# Codex's own plugin format, `.codex-plugin/plugin.json`. Qualification is the
# same `name`-is-a-string test Cursor's native format uses, and the manifest is
# the same shape `claude_plugin.parse` already reads — Codex's bundles are
# Claude-Code-shaped, which is the whole finding behind ADR-0055.
_CODEX_PLUGIN_FORMAT = PluginFormat(
    manifest_dir=".codex-plugin",
    manifest_filename="plugin.json",
    detect=_detect_named_plugin_manifest,
    parse=claude_plugin.parse,
)

CURSOR_SURFACE = RepoSurface(
    config_dir=".cursor",
    # Ordered candidate list per docs/specs/cursor-agent-kind.md "Manifest
    # resolution": `.cursor-plugin/plugin.json` -> `.claude-plugin/plugin.json`
    # (Cursor also reads Claude Code's own manifest) -> root `plugin.json`
    # (Agent Plugins, content-identified via `$schema`). The `.claude-plugin`
    # candidate is `CLAUDE_CODE_SURFACE`'s own format object, reused verbatim
    # so the two kinds can never disagree about what qualifies it.
    plugin_formats=(
        _CURSOR_PLUGIN_FORMAT,
        CLAUDE_CODE_SURFACE.plugin_formats[0],
        _AGENT_PLUGINS_FORMAT,
    ),
    # Cursor Plugins' bundled contract mirrors Claude Code's own plugin
    # bundle shape, with one deliberate crossing (ADR-0053): folder discovery
    # accepts root `mcp.json` OR `.mcp.json` (docs/specs/cursor-agent-kind.md:283),
    # tried in this order — `mcp.json` first, since it's Cursor's own default.
    bundled=BundledLayout(
        skills_dir="skills",
        mcp_filenames=("mcp.json", ".mcp.json"),
        hooks_filename="hooks/hooks.json",
        commands_dir="commands",
        agents_dir="agents",
    ),
    # Unused by Cursor's own composer — carried for dataclass completeness,
    # matching CLAUDE_CODE_SURFACE's values so a stray read isn't silently
    # wrong.
    settings_filename="settings.json",
    project_skills_subdir="skills",
    standalone_mcp_filenames=(),
    manifest_optional=True,
    excludes_plugin_owned_content=True,
    command_agent_surfaces=(),
    # Cursor's own vendor-built-in skill root is a SIBLING of `skills/`
    # (`<config_dir>/skills-cursor/`), never nested inside it — Cursor's own
    # docs say to ignore it (docs/specs/cursor-agent-kind.md "Exclusions").
    skill_config_dirs=(".cursor", ".agents", ".claude", ".codex"),
    excluded_skill_dirs=("skills-cursor",),
    scoped_mcp_rels=(".cursor/mcp.json",),
    settings_rel=None,
)

# Public alias (ADR-0053: a cross-module private import is never the
# contract): `graph_build_cursor` needs to recognize which realized
# candidate is the portable Agent Plugins format, to route it through
# `agent_plugins.parse` instead of the `claude_plugin`-shaped plugin branch,
# and to apply the "strictly below a realized native root" exclusion.
AGENT_PLUGINS_FORMAT = _AGENT_PLUGINS_FORMAT


CODEX_SURFACE = RepoSurface(
    config_dir=".codex",
    # Ordered candidate list per docs/specs/codex-agent-kind.md "Plugins":
    # `.codex-plugin/plugin.json` first, then Claude Code's own manifest. The
    # order is load-bearing, not cosmetic — a bundle carrying both has
    # genuinely different content in each (on the audited endpoint the
    # `.codex-plugin` manifest declares `skills`, `hooks`, and an `interface`
    # block the `.claude-plugin` one lacks), and 4 of the endpoint's cached
    # bundles carry ONLY Claude's, which is what proves the fallback live
    # rather than vestigial. `CLAUDE_CODE_SURFACE`'s format object is reused
    # verbatim so the two kinds can never disagree about what qualifies it.
    plugin_formats=(
        _CODEX_PLUGIN_FORMAT,
        CLAUDE_CODE_SURFACE.plugin_formats[0],
    ),
    # Codex's bundles are Claude-Code-shaped: same `skills/`, same
    # `hooks/hooks.json` with the same PascalCase event names, same `agents/`.
    bundled=BundledLayout(
        skills_dir="skills",
        mcp_filenames=(".mcp.json", "mcp.json"),
        hooks_filename="hooks/hooks.json",
        commands_dir="commands",
        agents_dir="agents",
    ),
    # `None`, not `"config.toml"`: Codex's settings-equivalent is TOML, read
    # directly by `tools/parsers/codex_config.py` (via
    # `_add_codex_declared_config_mcps`/`_add_codex_declared_config_hooks`),
    # not the JSON-only `claude_settings.parse` this field feeds. Naming it
    # here routed every `.codex/config.toml` through `json.loads`, turning a
    # valid Codex repo's own config into a spurious parse-failure gap.
    settings_filename=None,
    project_skills_subdir="skills",
    # Deliberately empty: Codex's MCP servers are declared INSIDE
    # `config.toml` as `[mcp_servers.*]`, never as a standalone manifest. A
    # bare `mcp.json` pattern here would claim files Codex does not read and
    # recreate the cross-kind collision the per-agent-graph model prevents.
    standalone_mcp_filenames=(),
    scoped_mcp_rels=(),
    # No commands surface. Both other kinds have one; Codex does not, and
    # shipping a parser for it would report components no agent has.
    command_agent_surfaces=(),
    # `.agents/skills` as well as `.codex/skills`: Codex's published skills
    # reference lists repository `.agents/skills` (walked from the working
    # directory up to the repo root) and `$HOME/.agents/skills` among its
    # discovery locations. An earlier spec line said `.agents/skills` was not
    # read, on the evidence that the audited binary contains no such string
    # literal — the same non-inference that made the `[agents.*]` claim wrong.
    skill_config_dirs=(".codex", ".agents"),
    # `<root>/skills/.system/` IS excluded, but structurally — by its
    # `.codex-system-skills.marker` file, checked during composition — not by
    # name here. A name list is what Cursor does for these same six built-ins,
    # and its own spec flags that list as drift-prone.
    excluded_skill_dirs=(),
    settings_rel=None,
    manifest_optional=False,
    excludes_plugin_owned_content=True,
)
