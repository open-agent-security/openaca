"""The Codex kind (ADR-0055). Mirrors `tools/agent_kinds/cursor.py`."""

from __future__ import annotations

import os
from pathlib import Path

from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext, matches_evidence
from tools.graph import Graph
from tools.parsers import CODEX_MANIFEST_REGISTRY, HOST_AGNOSTIC_REGISTRY
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.posture import (
    collect_codex_endpoint_mcp_manifests,
    collect_codex_mcp_manifests,
    collect_codex_project_trust_manifests,
    collect_codex_rules_manifests,
    no_manifests,
)
from tools.posture.rules import (
    command_policy_allow,
    insecure_transport,
    mutable_install,
    project_trust,
    skill_capability,
)
from tools.repo_surface import CODEX_SURFACE

KIND_ID = "codex"
DISPLAY_NAME = "Codex"
ROOT_LABEL = "codex"

# A baseline is argued from a named gap at THAT source, never inherited from
# the other and never set conservatively because the kind is new.
#
# `declared` is `complete`: all four surfaces a Codex repo declares —
# `.codex/config.toml`, `.codex/hooks.json`, `.codex/skills/**/SKILL.md`, and
# both plugin manifest formats — parse in full, and none is conditional on
# state a scan cannot read. Cursor is `partial` here for a real reason Codex
# does not share: its extensibility flag lives in an editor state database, so
# a Cursor repo scan cannot tell whether the compat skills it reports load.
#
# `installed` is `complete` too, once the profile layer is read. Under the
# coverage rule — a gap lowers composition coverage only if it can hide a
# COMPONENT — nothing about a Codex endpoint is undetectable:
#   * profile files (`<root>/<name>.config.toml`) DID hide MCP servers, and are
#     now composed, which is what closed this;
#   * `.rules` and `[projects.*]` declare no components (posture, own rule ids);
#   * marketplace-registry gaps cost identity, not enumeration;
#   * runtime MCP registration has ZERO references in the audited binary — an
#     earlier draft asserted it by carrying the claim over from Cursor, where it
#     is real, without checking it here.
#   * `/etc/codex/skills` and a `--config-dir`-overridden `$HOME/.agents/skills`
#     CAN each hide real skill components, unlike the items above — so those
#     two are not baseline exceptions but runtime checks
#     (`_record_codex_admin_skills_gap`, the override branch in
#     `build_codex_installed_graph`, ADR-0059) that degrade an affected scan to
#     `partial` via `graph.record_gap` while an unaffected one stays `complete`.
# `managed_config.toml` remains unaudited, and is the one thing that would
# reopen this: it is a file, so if it turns out to declare components the
# honest response is to read it, not to relabel.
COVERAGE_BASELINE = {"installed": "complete", "declared": "complete"}

# Codex-owned surfaces only. `.claude-plugin/plugin.json` is deliberately
# absent: Codex READS it as the second plugin manifest candidate, but a tree
# carrying only Claude Code's manifest declares a Claude Code agent, not a
# Codex one — the same composition-versus-evidence separation `cursor.py`
# draws. `AGENTS.md` is absent because instruction files are not configuration
# (spec: "Not configuration"), the same rule Claude Code applies to its own
# `CLAUDE.md`.
_DECLARED_EVIDENCE_PATTERNS: tuple[str, ...] = (
    ".codex/config.toml",
    "*/.codex/config.toml",
    ".codex/hooks.json",
    "*/.codex/hooks.json",
    ".codex/skills/*/SKILL.md",
    "*/.codex/skills/*/SKILL.md",
    # Codex reads the cross-tool `.agents/` convention directory as well. It is
    # evidence for every kind that genuinely reads it — see ADR-0058, which
    # supersedes ADR-0052's "sole reader" exception now that a second kind does.
    ".agents/skills/*/SKILL.md",
    "*/.agents/skills/*/SKILL.md",
    ".codex-plugin/plugin.json",
    "*/.codex-plugin/plugin.json",
)


def _realized_plugin_roots(scan_root: Path, *, include_gitignored: bool) -> list[Path]:
    """Directories where one of Codex's plugin formats actually realizes.

    Thin wrapper over the one implementation of that computation, so evidence
    detection can never drift from what composition itself excludes.
    """
    # Local import: agent_kinds -> graph_build* stays one-way (see `_compose`).
    from tools.graph_build_cursor import realized_plugin_roots

    return realized_plugin_roots(scan_root, CODEX_SURFACE, include_gitignored=include_gitignored)


def _matches_evidence(rel: str, path: Path, realized_roots: list[Path]) -> bool:
    # Content of an already-realized plugin is composition's, not an
    # independent Codex declaration — whatever its shape. A bundled fixture
    # `examples/.codex/config.toml` must not trip a phantom Codex BOM for a
    # repo with no Codex-owned surface of its own.
    from tools.graph_build_cursor import is_owned_by_realized_plugin

    if is_owned_by_realized_plugin(path, realized_roots, CODEX_SURFACE):
        return False
    return matches_evidence(rel, _DECLARED_EVIDENCE_PATTERNS)


def declared_evidence(scan_root: Path, *, include_gitignored: bool = False) -> Path | None:
    """The first file proving this tree declares a Codex agent, else None."""
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


def resolve_config_root(config_dir: Path | None = None) -> Path:
    """`--config-dir`, else `$CODEX_HOME`, else `<home>/.codex` (ADR-0056).

    Unlike Cursor, Codex declares a genuinely relocatable root: one variable
    moves the whole tree, and Codex reads no other runtime's config, so naming
    a directory fully specifies the target. `root_override_refusal` is
    therefore `None` — see ADR-0056 for why ADR-0054's refusal was
    Cursor-specific rather than the general rule.
    """
    if config_dir is not None:
        return config_dir.expanduser()
    env_root = os.environ.get("CODEX_HOME")
    if env_root:
        # `~` arrives unexpanded from any non-shell setter (Docker `ENV`, an MDM
        # profile, a config file), and `Path("~/.codex")` is a directory named
        # `~` — discovery would then report no agent, with exit 0.
        return Path(env_root).expanduser()
    return Path.home() / ".codex"


def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
    if ctx.source == "installed":
        return _discover_installed(ctx)
    return _discover_declared(ctx)


def _discover_installed(ctx: DiscoveryContext) -> list[AgentInstance]:
    """The runtime's own config root existing is the evidence (ADR-0044).

    An installed runtime with no configuration is a real agent with zero
    components, so an empty directory still yields an instance — the asymmetry
    with `declared` is deliberate.
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
            config_root_overridden=ctx.config_dir is not None,
        )
    ]


def _discover_declared(ctx: DiscoveryContext) -> list[AgentInstance]:
    if ctx.scan_root is None:
        return []
    evidence = declared_evidence(ctx.scan_root, include_gitignored=ctx.include_gitignored)
    if evidence is None:
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
    """Lazy import keeps ADR-0044's one-way dependency: `agent_kinds` may
    import `graph_build`, never the reverse."""
    from tools.graph_build import build_codex_declared_graph, build_codex_installed_graph

    if agent.source == "installed":
        assert agent.config_root is not None
        return build_codex_installed_graph(
            agent.config_root,
            agent.project_root,
            root_key=agent.bom_ref,
            root_label=agent.root_label,
            include_gitignored=include_gitignored,
            warnings=warnings,
            config_root_overridden=agent.config_root_overridden,
        )
    return build_codex_declared_graph(
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
    # Not `api_endpoint_override` — it matches literal Anthropic settings keys
    # in a file Codex does not have. Not `mcp_auto_approve` either: neither of
    # Codex's policy surfaces names an MCP server, and the two rules below
    # cover them under their own ids (spec: "Posture rule applicability").
    root_override_refusal=None,
    posture_rules=frozenset(
        {
            insecure_transport.RULE_ID,
            mutable_install.RULE_ID,
            skill_capability.RULE_ID,
            command_policy_allow.RULE_ID,
            project_trust.RULE_ID,
        }
    ),
    manifest_patterns=tuple(HOST_AGNOSTIC_REGISTRY) + tuple(CODEX_MANIFEST_REGISTRY),
    repo_surface=CODEX_SURFACE,
    # `insecure_transport` reads the composed MCP refs at both sources, the
    # same rule the Cursor collectors follow. The settings slot has nothing to
    # fill: neither `mcp_auto_approve` nor `api_endpoint_override` is in
    # Codex's allowlist above, and `no_manifests` is the shared no-op for a
    # kind with no filesystem-shaped surface for that slot.
    posture_manifest_collectors=(collect_codex_mcp_manifests, no_manifests),
    installed_posture_collectors=(collect_codex_endpoint_mcp_manifests, no_manifests),
    extra_installed_posture_collectors={
        command_policy_allow.RULE_ID: collect_codex_rules_manifests,
        project_trust.RULE_ID: collect_codex_project_trust_manifests,
    },
)
