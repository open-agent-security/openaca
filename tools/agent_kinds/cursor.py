"""The Cursor kind (ADR-0052). Mirrors `tools/agent_kinds/claude_code.py`."""

from __future__ import annotations

from pathlib import Path

from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext, matches_evidence
from tools.graph import Graph
from tools.parsers import CURSOR_MANIFEST_REGISTRY, HOST_AGNOSTIC_REGISTRY
from tools.parsers.agent_plugins import is_agent_plugins_manifest
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.posture import (
    collect_cursor_endpoint_mcp_manifests,
    collect_cursor_endpoint_permissions_manifests,
    collect_cursor_mcp_manifests,
    collect_cursor_permissions_manifests,
)
from tools.posture.rules import (
    insecure_transport,
    mcp_auto_approve,
    mutable_install,
    skill_capability,
)
from tools.repo_surface import CURSOR_SURFACE

KIND_ID = "cursor"
DISPLAY_NAME = "Cursor"
ROOT_LABEL = "cursor"

COVERAGE_BASELINE = {"installed": "partial", "declared": "partial"}

# Cursor-owned surfaces only (docs/specs/cursor-agent-kind.md "Files Cursor
# reads that another runtime owns"). `.claude/*` and `.codex/*` are Cursor
# COMPOSITION (it reads them, gated by the extensibility flag) but never
# EVIDENCE: a tree containing only `.claude/agents/` declares a Claude Code
# agent, not a Cursor one, and treating a compat-read path as evidence would
# emit a phantom near-empty Cursor BOM for every Claude-only repository.
# `.agents/skills/` is the one exception — Cursor is currently the only
# registered kind that reads it, so it *is* evidence (revisit if a Codex kind
# lands). A bare `mcp.json`/`.mcp.json` is excluded for the same reason
# `_DECLARED_EVIDENCE_PATTERNS` excludes it in `claude_code.py`: no kind owns
# it exclusively.
_DECLARED_EVIDENCE_PATTERNS: tuple[str, ...] = (
    ".cursor/mcp.json",
    "*/.cursor/mcp.json",
    ".cursor/skills/*/SKILL.md",
    "*/.cursor/skills/*/SKILL.md",
    ".agents/skills/*/SKILL.md",
    "*/.agents/skills/*/SKILL.md",
    ".cursor/commands/*",
    "*/.cursor/commands/*",
    ".cursor/agents/*",
    "*/.cursor/agents/*",
    ".cursor-plugin/plugin.json",
    "*/.cursor-plugin/plugin.json",
)


def _realized_plugin_roots(scan_root: Path, *, include_gitignored: bool) -> list[Path]:
    """Directories where a Cursor plugin format — native (`.cursor-plugin`,
    or the reused `.claude-plugin`) or Agent Plugins — actually realizes:
    thin wrapper around `tools.graph_build_cursor.realized_plugin_roots`, the
    one place that computation is implemented, so evidence detection can
    never drift from what composition itself excludes.

    Any of those formats can carry a nested fixture of either format (e.g. a
    bundled `examples/demo/plugin.json` or `examples/demo/.cursor-plugin/
    plugin.json` test fixture) that is bundle content, not a second,
    independent Cursor declaration — composition already excludes it from
    realizing as its own plugin. Evidence detection has to draw the same
    boundary, or a repo with no Cursor-owned surface at all can still trip a
    phantom Cursor BOM off its own unrelated fixture file (the outer plugin
    is itself never evidence — see `_DECLARED_EVIDENCE_PATTERNS` above — so
    nothing bundled inside it should be either).
    """
    # Local import: agent_kinds -> graph_build* stays one-way (see `_compose`).
    from tools.graph_build_cursor import realized_plugin_roots

    return realized_plugin_roots(scan_root, CURSOR_SURFACE, include_gitignored=include_gitignored)


def _matches_evidence(rel: str, path: Path, realized_roots: list[Path]) -> bool:
    # Anything that is content of an already-realized plugin is composition's,
    # not an independent Cursor declaration — whatever its shape. Testing this
    # BEFORE the pattern match (and for every path, not just `plugin.json`) is
    # what keeps a bundled fixture `examples/.cursor/mcp.json`,
    # `.cursor/commands/demo.md`, or `.cursor/skills/demo/SKILL.md` from
    # tripping a phantom Cursor BOM off a repo with no Cursor-owned surface at
    # all. The outer plugin is itself never evidence (see
    # `_DECLARED_EVIDENCE_PATTERNS`), so nothing bundled inside it can be.
    from tools.graph_build_cursor import is_owned_by_realized_plugin

    if is_owned_by_realized_plugin(path, realized_roots):
        return False
    if matches_evidence(rel, _DECLARED_EVIDENCE_PATTERNS):
        return True
    # A root `plugin.json` (Agent Plugins format) is evidence only when its
    # content actually declares the schema — a glob on the filename alone
    # would treat any unrelated `plugin.json` as a Cursor agent.
    return path.name == "plugin.json" and is_agent_plugins_manifest(path)


def declared_evidence(scan_root: Path, *, include_gitignored: bool = False) -> Path | None:
    """The first file proving this tree declares a Cursor agent, else None.

    The walk is the same gitignore-aware walk the repo scan uses, so evidence
    and composition never disagree about what is in scope. A declaration
    inside an ignored directory is invisible to both unless
    `--include-gitignored` is set.
    """
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
    "an installed Cursor's composition is gathered from three places — its own "
    "root, permissions.json (relocated independently), and another runtime's "
    "skill roots under your home — and a root override moves only the first, "
    "producing a composition stitched from two homes that the output cannot "
    "distinguish from a correct scan"
)


def resolve_config_root(config_dir: Path | None = None) -> Path:
    """Always `<home>/.cursor` — Cursor declares no relocatable root (ADR-0054).

    `config_dir` is accepted and ignored so this keeps the shape every kind's
    resolver has; the CLI rejects the flag before discovery, so a non-None value
    here would mean that guard was bypassed rather than that a root was chosen.

    No environment variable analogous to `$CLAUDE_CONFIG_DIR` exists.
    `CURSOR_CONFIG_DIR` scopes only `permissions.json` and the CLI's own
    config (docs/specs/cursor-agent-kind.md "Config root"); honouring it as a
    whole-root override would look in the wrong place for every other
    composition surface, so it is deliberately not read here.
    """
    return Path.home() / ".cursor"


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
    # Local import keeps the one-way dependency: agent_kinds -> graph_build*.
    from tools.graph_build_cursor import build_cursor_graph

    return build_cursor_graph(agent, include_gitignored=include_gitignored, warnings=warnings)


KIND = AgentKind(
    id=KIND_ID,
    display_name=DISPLAY_NAME,
    cardinality="singleton",
    root_label=ROOT_LABEL,
    coverage_baseline=COVERAGE_BASELINE,
    discover=discover,
    compose=_compose,
    # Not `api_endpoint_override` — it matches literal Anthropic settings
    # keys in a file Cursor does not have (docs/specs/cursor-agent-kind.md
    # "Posture rule applicability").
    root_override_refusal=ROOT_OVERRIDE_REFUSAL,
    posture_rules=frozenset(
        {
            insecure_transport.RULE_ID,
            mutable_install.RULE_ID,
            skill_capability.RULE_ID,
            mcp_auto_approve.RULE_ID,
        }
    ),
    manifest_patterns=tuple(HOST_AGNOSTIC_REGISTRY) + tuple(CURSOR_MANIFEST_REGISTRY),
    repo_surface=CURSOR_SURFACE,
    posture_manifest_collectors=(
        collect_cursor_mcp_manifests,
        collect_cursor_permissions_manifests,
    ),
    installed_posture_collectors=(
        collect_cursor_endpoint_mcp_manifests,
        collect_cursor_endpoint_permissions_manifests,
    ),
)
