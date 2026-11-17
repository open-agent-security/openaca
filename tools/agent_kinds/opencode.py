"""The OpenCode kind (ADR-0083). Mirrors `tools/agent_kinds/devin_cli.py`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tools.agent_kinds import AgentInstance, AgentKind, Builtins, DiscoveryContext
from tools.graph import Graph
from tools.parsers import HOST_AGNOSTIC_REGISTRY, OPENCODE_MANIFEST_REGISTRY
from tools.parsers.gitignore import iter_unignored_relative_files
from tools.posture import (
    collect_opencode_endpoint_mcp_manifests,
    collect_opencode_mcp_manifests,
    no_manifests,
)
from tools.posture.rules import insecure_transport, mcp_header_credential, mutable_install

KIND_ID = "opencode"
DISPLAY_NAME = "OpenCode"
ROOT_LABEL = "opencode"

# Argued per source, from a named gap at that source (ADR-0083):
#   * both — a plugin is code: its `config` hook receives the merged
#     configuration and its `tool` hook adds tools, so a file scan cannot see
#     what a plugin adds.
#   * installed — MCP servers registered at run time (`POST /mcp`, ACP
#     `session/new`), remote configuration, and the process environment.
COVERAGE_BASELINE = {"installed": "partial", "declared": "partial"}

ROOT_OVERRIDE_REFUSAL = (
    "an installed OpenCode's composition is gathered from five places — its "
    "config root, its plugin cache under a different XDG variable, ~/.opencode, "
    "other runtimes' skill roots under your home, and a system managed directory "
    "— and a root override moves only the first, producing a composition stitched "
    "from two homes that the output cannot distinguish from a correct scan"
)


def declared_evidence(scan_root: Path, *, include_gitignored: bool = False) -> Path | None:
    """The first file proving this tree declares an OpenCode agent, else None.
    The classifier is the one declared composition uses."""
    # Local import: agent_kinds -> graph_build* stays one-way (see `_compose`).
    from tools.graph_build_opencode import is_declared_evidence

    for rel, path, _spec in iter_unignored_relative_files(
        scan_root, include_gitignored=include_gitignored
    ):
        if is_declared_evidence(tuple(rel.split("/"))):
            return path
    return None


def _xdg_root(variable: str, default: Path) -> Path:
    """`$<variable>` when it names an absolute path, else `default`. OpenCode
    resolves a relative value against its own working directory, which a scan
    cannot know."""
    value = os.environ.get(variable)
    if value and Path(value).is_absolute():
        return Path(value)
    return default


def resolve_config_root(config_dir: Path | None = None) -> Path:
    """`$XDG_CONFIG_HOME/opencode`, else `~/.config/opencode`.

    `config_dir` is accepted and ignored so this keeps the shape every kind's
    resolver has; the CLI rejects the flag before discovery.
    """
    return _xdg_root("XDG_CONFIG_HOME", Path.home() / ".config") / "opencode"


def resolve_cache_root() -> Path:
    """`$XDG_CACHE_HOME/opencode`, else `~/.cache/opencode`: the npm plugin
    installs' parent, moved by a different variable from the config root."""
    return _xdg_root("XDG_CACHE_HOME", Path.home() / ".cache") / "opencode"


def resolve_managed_root() -> Path:
    """The system directory OpenCode merges last (`config/managed.ts`)."""
    if sys.platform == "darwin":
        return Path("/Library/Application Support/opencode")
    return Path("/etc/opencode")


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
    from tools.graph_build_opencode import (
        build_opencode_declared_graph,
        build_opencode_installed_graph,
    )

    if agent.source == "installed":
        return build_opencode_installed_graph(
            agent,
            cache_root=resolve_cache_root(),
            managed_root=resolve_managed_root(),
            home=Path.home(),
            include_gitignored=include_gitignored,
            warnings=warnings,
        )
    return build_opencode_declared_graph(
        agent, include_gitignored=include_gitignored, warnings=warnings
    )


# What OpenCode v1.19.5 defines itself (ADR-0076): the `native: true` agent
# definitions in `packages/opencode/src/agent/agent.ts`, and
# `CUSTOMIZE_OPENCODE_SKILL_NAME` in `packages/opencode/src/skill/index.ts`.
_BUILTINS = Builtins(
    agents=("build", "compaction", "explore", "general", "plan", "summary", "title"),
    skills=("customize-opencode",),
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
    # Not `mcp_auto_approve` or `command_policy_allow`: OpenCode allows every
    # tool by default, so a `permission` allow is no opt-in. Not
    # `skill_capability`: OpenCode ignores `allowed-tools` (ADR-0085). Not
    # `project_trust` or `api_endpoint_override`: OpenCode has neither surface.
    posture_rules=frozenset(
        {
            insecure_transport.RULE_ID,
            mcp_header_credential.RULE_ID,
            mutable_install.RULE_ID,
        }
    ),
    manifest_patterns=tuple(HOST_AGNOSTIC_REGISTRY) + tuple(OPENCODE_MANIFEST_REGISTRY),
    posture_manifest_collectors=(collect_opencode_mcp_manifests, no_manifests),
    installed_posture_collectors=(collect_opencode_endpoint_mcp_manifests, no_manifests),
    builtins=lambda _agent: _BUILTINS,
)
