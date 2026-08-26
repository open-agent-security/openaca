"""The Claude Code kind. The only registered kind (ADR-0044)."""

from __future__ import annotations

import os
from pathlib import Path

from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext, matches_evidence
from tools.graph import Graph
from tools.parsers import REGISTRY as _MANIFEST_REGISTRY
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.posture import (
    collect_endpoint_mcp_manifests,
    collect_endpoint_settings_manifests,
    collect_mcp_manifests,
    collect_settings_manifests,
)

KIND_ID = "claude-code"
DISPLAY_NAME = "Claude Code"
ROOT_LABEL = "claude-code"

COVERAGE_BASELINE = {"installed": "complete", "declared": "complete"}

# Files Claude Code owns. Evidence of a *declared* agent is one of these
# existing (ADR-0044); a bare `mcp.json` is excluded because no kind owns it
# exclusively, so on its own it declares no agent. Deliberately narrower than
# every file under `.claude/`: this list is recognized composition surfaces,
# not arbitrary content (a `.claude/CLAUDE.md` alone is not evidence). It is
# not required to match `tools/parsers/__init__.py`'s composition patterns —
# evidence answers "does an agent exist", composition answers "what does it
# contain" — but the entries here name the same surfaces that module parses.
_DECLARED_EVIDENCE_PATTERNS: tuple[str, ...] = (
    ".mcp.json",
    "*/.mcp.json",
    ".claude/settings.json",
    "*/.claude/settings.json",
    ".claude/settings.local.json",
    "*/.claude/settings.local.json",
    ".claude/skills/*/SKILL.md",
    "*/.claude/skills/*/SKILL.md",
    ".claude/commands/*",
    "*/.claude/commands/*",
    ".claude/agents/*",
    "*/.claude/agents/*",
    ".claude-plugin/plugin.json",
    "*/.claude-plugin/plugin.json",
)


def _matches_evidence(rel: str) -> bool:
    return matches_evidence(rel, _DECLARED_EVIDENCE_PATTERNS)


def declared_evidence(scan_root: Path, *, include_gitignored: bool = False) -> Path | None:
    """The first file proving this tree declares a Claude Code agent, else None.

    The walk is the same gitignore-aware walk the repo scan uses, so evidence and
    composition never disagree about what is in scope. A declaration inside an
    ignored directory is invisible to both unless `--include-gitignored` is set.
    """
    spec = None if include_gitignored else load_gitignore_spec(scan_root)
    for path in iter_unignored_files(scan_root, spec):
        try:
            rel = path.relative_to(scan_root).as_posix()
        except ValueError:
            continue
        if _matches_evidence(rel):
            return path
    return None


def resolve_config_root(config_dir: Path | None) -> Path:
    """Explicit `--config-dir` wins, then `$CLAUDE_CONFIG_DIR`, then `~/.claude`."""
    if config_dir is not None:
        return config_dir.expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
    if ctx.source == "installed":
        return _discover_installed(ctx)
    return _discover_declared(ctx)


def _discover_installed(ctx: DiscoveryContext) -> list[AgentInstance]:
    """The runtime's own config root existing is the evidence (ADR-0044).

    An installed runtime with no configuration is a real agent with zero
    components, so an empty directory still yields an instance here — the
    asymmetry with `declared` is deliberate.
    """
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
    # Local import keeps the one-way dependency: agent_kinds -> graph_build.
    from tools.graph_build import build_rooted_graph

    if agent.source == "installed":
        assert agent.config_root is not None
        return build_rooted_graph(
            agent.config_root,
            "endpoint",
            root_key=agent.bom_ref,
            root_label=agent.root_label,
            project_root=agent.project_root,
            include_gitignored=include_gitignored,
            warnings=warnings,
        )
    assert agent.scan_root is not None
    return build_rooted_graph(
        agent.scan_root,
        "repo",
        root_key=agent.bom_ref,
        root_label=agent.root_label,
        include_gitignored=include_gitignored,
        warnings=warnings,
    )


KIND = AgentKind(
    id=KIND_ID,
    display_name=DISPLAY_NAME,
    cardinality="singleton",
    root_label=ROOT_LABEL,
    coverage_baseline=COVERAGE_BASELINE,
    discover=discover,
    compose=_compose,
    posture_rules=None,  # None = every rule applies; an allowlist is per-kind
    # The kind's declared manifest surface is today's whole registry and all
    # four posture collectors — byte-identical to what `scan endpoint`/`scan
    # repo` already read, since Claude Code is still the only kind. A future
    # second kind registers its own subset here instead of these module-level
    # functions being called unconditionally for every agent regardless of kind.
    manifest_patterns=tuple(_MANIFEST_REGISTRY),
    posture_manifest_collectors=(collect_mcp_manifests, collect_settings_manifests),
    installed_posture_collectors=(
        collect_endpoint_mcp_manifests,
        collect_endpoint_settings_manifests,
    ),
)
