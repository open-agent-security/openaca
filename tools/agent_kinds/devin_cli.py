"""The Devin CLI kind (ADR-0070). Mirrors `tools/agent_kinds/cursor.py`."""

from __future__ import annotations

import os
from pathlib import Path

from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext, matches_evidence
from tools.graph import Graph
from tools.parsers import DEVIN_MANIFEST_REGISTRY, HOST_AGNOSTIC_REGISTRY
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.posture import (
    collect_devin_endpoint_mcp_manifests,
    collect_devin_endpoint_permissions_manifests,
    collect_devin_mcp_manifests,
    collect_devin_permissions_manifests,
)
from tools.posture.rules import (
    command_policy_allow,
    insecure_transport,
    mcp_auto_approve,
    mcp_header_credential,
    mutable_install,
    skill_capability,
)
from tools.repo_surface import DEVIN_AGENT_DIRECTORY_FILENAMES, DEVIN_SURFACE

KIND_ID = "devin-cli"
DISPLAY_NAME = "Devin CLI"
ROOT_LABEL = "devin-cli"

# Argued per source, from a named gap at that source (ADR-0070):
#   * installed — managed org and personal plugins arrive from Devin's service
#     and are visible only where cached, and an ACP client can register MCP
#     servers at `session/new` that appear in no file. Neither closes by
#     parsing.
#   * declared — the user's `read_config_from` lives outside the repository,
#     so whether the imported `.claude/*`, `.cursor/*`, `.windsurf/*` and
#     `.github/skills` load is unknown to a repo scan.
COVERAGE_BASELINE = {"installed": "partial", "declared": "partial"}

# Devin-owned surfaces, plus the two shared `.agents/` directories it reads
# (ADR-0058, ADR-0072). Everything Devin imports from another runtime is
# composition, never evidence: a tree holding only `.claude/skills/` declares a
# Claude Code agent. The gitignored `.devin/*.local.json` files are
# composition too — their presence in a tree is incidental. Instruction files
# (`AGENTS.md`, `.devin/rules/`) are not configuration. The `agents/` globs
# are narrowed to the flat `*.md` and directory-form filenames composition
# loads (`_is_flat_agent`/`_is_directory_agent`) — an unrelated file such as
# `agents/notes.txt` loads nothing and must not count as evidence.
_DECLARED_EVIDENCE_PATTERNS: tuple[str, ...] = tuple(
    pattern
    for owned in (
        ".devin/mcp_config.json",
        ".devin/config.json",
        ".devin/hooks.v1.json",
        ".devin/skills/*/SKILL.md",
        ".cognition/skills/*/SKILL.md",
        ".devin/agents/*.md",
        *(f".devin/agents/*/{name}" for name in DEVIN_AGENT_DIRECTORY_FILENAMES),
        ".devin-plugin/plugin.json",
        ".agents/skills/*/SKILL.md",
        ".agents/agents/*.md",
        *(f".agents/agents/*/{name}" for name in DEVIN_AGENT_DIRECTORY_FILENAMES),
    )
    for pattern in (owned, f"*/{owned}")
)


def _realized_plugin_roots(scan_root: Path, *, include_gitignored: bool) -> list[Path]:
    """Directories where one of Devin's plugin formats realizes — the one
    implementation composition also uses, so evidence cannot drift from it."""
    # Local import: agent_kinds -> graph_build* stays one-way (see `_compose`).
    from tools.graph_build_cursor import realized_plugin_roots

    return realized_plugin_roots(scan_root, DEVIN_SURFACE, include_gitignored=include_gitignored)


def _matches_evidence(rel: str, path: Path, realized_roots: list[Path]) -> bool:
    # Content of an already-realized plugin is that plugin's, not an
    # independent Devin declaration. A plugin's own manifest stays visible.
    from tools.graph_build_cursor import is_owned_by_realized_plugin

    if is_owned_by_realized_plugin(path, realized_roots, DEVIN_SURFACE):
        return False
    return matches_evidence(rel, _DECLARED_EVIDENCE_PATTERNS)


def declared_evidence(scan_root: Path, *, include_gitignored: bool = False) -> Path | None:
    """The first file proving this tree declares a Devin CLI agent, else None."""
    spec = None if include_gitignored else load_gitignore_spec(scan_root)
    realized_roots = _realized_plugin_roots(scan_root, include_gitignored=include_gitignored)
    for path in iter_unignored_files(scan_root, spec):
        try:
            rel = path.relative_to(scan_root).as_posix()
        except ValueError:
            continue
        if _matches_evidence(rel, path, realized_roots):
            return path
    return None


ROOT_OVERRIDE_REFUSAL = (
    "an installed Devin CLI's composition is gathered from four places — its "
    "config root, its plugin store under a different XDG variable, "
    "~/.agents/skills, and other runtimes' trees under your home — and a root "
    "override moves only the first, producing a composition stitched from two "
    "homes that the output cannot distinguish from a correct scan"
)


def _xdg_root(variable: str, default: Path) -> Path:
    """`$<variable>` when it names an absolute path, else `default`. The XDG
    base-directory specification treats a relative value as invalid."""
    value = os.environ.get(variable)
    if value and Path(value).is_absolute():
        return Path(value)
    return default


def resolve_config_root(config_dir: Path | None = None) -> Path:
    """`$XDG_CONFIG_HOME/devin`, else `~/.config/devin` (ADR-0071).

    `config_dir` is accepted and ignored so this keeps the shape every kind's
    resolver has; the CLI rejects the flag before discovery.
    """
    return _xdg_root("XDG_CONFIG_HOME", Path.home() / ".config") / "devin"


def resolve_legacy_config_root() -> Path:
    """`$XDG_CONFIG_HOME/cognition`: the root's name before the rename, whose
    `skills/` Devin still lists."""
    return _xdg_root("XDG_CONFIG_HOME", Path.home() / ".config") / "cognition"


def resolve_data_root() -> Path:
    """`$XDG_DATA_HOME/devin/cli`, else `~/.local/share/devin/cli`: the plugin
    store's parent, moved by a different variable from the config root."""
    return _xdg_root("XDG_DATA_HOME", Path.home() / ".local" / "share") / "devin" / "cli"


def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
    if ctx.source == "installed":
        return _discover_installed(ctx)
    return _discover_declared(ctx)


def _discover_installed(ctx: DiscoveryContext) -> list[AgentInstance]:
    """The runtime's own config root existing is the evidence (ADR-0044)."""
    root = resolve_config_root(ctx.config_dir)
    if not root.is_dir():
        return []
    return [
        AgentInstance(
            kind_id=KIND_ID,
            display_name=DISPLAY_NAME,
            source="installed",
            root_label=ROOT_LABEL,
            coverage_baseline=COVERAGE_BASELINE["installed"],
            config_root=root,
            project_root=ctx.project_root,
        )
    ]


def _discover_declared(ctx: DiscoveryContext) -> list[AgentInstance]:
    if ctx.scan_root is None:
        return []
    if declared_evidence(ctx.scan_root, include_gitignored=ctx.include_gitignored) is None:
        return []
    return [
        AgentInstance(
            kind_id=KIND_ID,
            display_name=DISPLAY_NAME,
            source="declared",
            root_label=ROOT_LABEL,
            coverage_baseline=COVERAGE_BASELINE["declared"],
            scan_root=ctx.scan_root,
        )
    ]


def _compose(
    agent: AgentInstance,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    # Local import keeps the one-way dependency: agent_kinds -> graph_build*.
    from tools.graph_build_devin import build_devin_declared_graph, build_devin_installed_graph

    if agent.source == "installed":
        return build_devin_installed_graph(
            agent,
            data_root=resolve_data_root(),
            legacy_config_root=resolve_legacy_config_root(),
            home=Path.home(),
            include_gitignored=include_gitignored,
            warnings=warnings,
        )
    return build_devin_declared_graph(
        agent, include_gitignored=include_gitignored, warnings=warnings
    )


KIND = AgentKind(
    id=KIND_ID,
    display_name=DISPLAY_NAME,
    cardinality="singleton",
    root_label=ROOT_LABEL,
    coverage_baseline=COVERAGE_BASELINE,
    discover=discover,
    compose=_compose,
    root_override_refusal=ROOT_OVERRIDE_REFUSAL,
    # Not `project_trust`: Devin has no trusted-directory concept. Not
    # `api_endpoint_override`: it matches literal Anthropic settings keys in a
    # file Devin does not have (spec: Posture). `mcp_auto_approve` and
    # `command_policy_allow` both read the one `permissions` list, split by
    # entry (ADR-0073).
    posture_rules=frozenset(
        {
            insecure_transport.RULE_ID,
            mcp_header_credential.RULE_ID,
            mutable_install.RULE_ID,
            skill_capability.RULE_ID,
            mcp_auto_approve.RULE_ID,
            command_policy_allow.RULE_ID,
        }
    ),
    manifest_patterns=tuple(HOST_AGNOSTIC_REGISTRY) + tuple(DEVIN_MANIFEST_REGISTRY),
    repo_surface=DEVIN_SURFACE,
    # The settings slot carries the merged `permissions` view, as Cursor's
    # carries `permissions.json`: it is read at both composition sources.
    posture_manifest_collectors=(collect_devin_mcp_manifests, collect_devin_permissions_manifests),
    installed_posture_collectors=(
        collect_devin_endpoint_mcp_manifests,
        collect_devin_endpoint_permissions_manifests,
    ),
)
