"""The Pi kind (ADR-0081). Mirrors `tools/agent_kinds/devin_cli.py`."""

from __future__ import annotations

import os
from pathlib import Path

from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext
from tools.graph import Graph
from tools.parsers import HOST_AGNOSTIC_REGISTRY, PI_MANIFEST_REGISTRY
from tools.parsers.gitignore import iter_unignored_relative_files
from tools.posture import (
    collect_pi_endpoint_mcp_manifests,
    collect_pi_mcp_manifests,
    collect_pi_project_trust_manifests,
    no_manifests,
)
from tools.posture.rules import (
    insecure_transport,
    mcp_header_credential,
    mutable_install,
    project_trust,
    skill_capability,
)

KIND_ID = "pi"
DISPLAY_NAME = "Pi"
ROOT_LABEL = "pi"

# `partial` at both sources (ADR-0081): an extension can call
# `pi.registerMcpServer()` and return skill and prompt paths from
# `resources_discover` at run time, and no file records either. That holds for
# a repository's own `.pi/extensions/` as much as for an installed agent's.
COVERAGE_BASELINE = {"installed": "partial", "declared": "partial"}

_EXTENSION_SUFFIXES = (".ts", ".js")
_EXTENSION_DIRECTORY_FILES = frozenset({"index.ts", "index.js", "package.json"})


def _skipped(name: str) -> bool:
    return name.startswith(".") or name == "node_modules"


def _is_loaded_skill(below_root: tuple[str, ...]) -> bool:
    """A `SKILL.md` that Pi's recursive skill walk reaches below its root."""
    return (
        bool(below_root)
        and below_root[-1] == "SKILL.md"
        and not any(_skipped(part) for part in below_root[:-1])
    )


def _is_pi_owned(below_pi: tuple[str, ...]) -> bool:
    head = below_pi[0]
    if len(below_pi) == 1:
        return head in ("settings.json", "mcp.json")
    if head == "skills":
        return _is_loaded_skill(below_pi[1:])
    if head == "prompts":
        return len(below_pi) == 2 and below_pi[1].endswith(".md") and not _skipped(below_pi[1])
    if head == "extensions" and not _skipped(below_pi[1]):
        if len(below_pi) == 2:
            return below_pi[1].endswith(_EXTENSION_SUFFIXES)
        return len(below_pi) == 3 and below_pi[2] in _EXTENSION_DIRECTORY_FILES
    return False


def is_evidence(rel: str) -> bool:
    """A Pi-owned file that loads, or a shared `.agents/skills` skill
    (ADR-0058). Instruction and display files (`.pi/SYSTEM.md`,
    `.pi/themes/`) declare no components and are not evidence."""
    parts = tuple(rel.split("/"))
    for index, part in enumerate(parts[:-1]):
        below = parts[index + 1 :]
        if part == ".pi" and _is_pi_owned(below):
            return True
        if part == ".agents" and below[0] == "skills" and _is_loaded_skill(below[1:]):
            return True
    return False


def declared_evidence(scan_root: Path, *, include_gitignored: bool = False) -> Path | None:
    """The first file proving this tree declares a Pi agent, else None."""
    for rel, path, _spec in iter_unignored_relative_files(
        scan_root, include_gitignored=include_gitignored
    ):
        if is_evidence(rel):
            return path
    return None


ROOT_OVERRIDE_REFUSAL = (
    "an installed Pi's composition comes from its agent directory, from "
    "$HOME/.agents/skills, and from npm packages that may sit under the global "
    "npm root, and a root override moves only the first; set "
    "PI_CODING_AGENT_DIR to scan a relocated agent directory the way Pi reads it"
)


def resolve_config_root(config_dir: Path | None = None) -> Path:
    """`$PI_CODING_AGENT_DIR` with a leading `~` expanded, else `~/.pi/agent`.

    `config_dir` is accepted and ignored so this keeps the shape every kind's
    resolver has; the CLI rejects the flag before discovery.
    """
    value = os.environ.get("PI_CODING_AGENT_DIR")
    if value:
        return Path(value).expanduser()
    return Path.home() / ".pi" / "agent"


def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
    if ctx.source == "installed":
        return _discover_installed(ctx)
    return _discover_declared(ctx)


def _discover_installed(ctx: DiscoveryContext) -> list[AgentInstance]:
    """The agent directory existing is the evidence (ADR-0044)."""
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
    from tools.graph_build_pi import build_pi_declared_graph, build_pi_installed_graph

    if agent.source == "installed":
        return build_pi_installed_graph(
            agent, home=Path.home(), include_gitignored=include_gitignored, warnings=warnings
        )
    return build_pi_declared_graph(agent, include_gitignored=include_gitignored, warnings=warnings)


KIND = AgentKind(
    id=KIND_ID,
    display_name=DISPLAY_NAME,
    cardinality="singleton",
    root_label=ROOT_LABEL,
    coverage_baseline=COVERAGE_BASELINE,
    discover=discover,
    compose=_compose,
    root_override_refusal=ROOT_OVERRIDE_REFUSAL,
    # Not `mcp_auto_approve` or `command_policy_allow`: Pi asks for no approval
    # before a tool call, so it has no allow list for either to read. Not
    # `api_endpoint_override`: it matches literal Anthropic settings keys in a
    # file Pi does not have (spec: Posture).
    posture_rules=frozenset(
        {
            insecure_transport.RULE_ID,
            mcp_header_credential.RULE_ID,
            mutable_install.RULE_ID,
            skill_capability.RULE_ID,
            project_trust.RULE_ID,
        }
    ),
    manifest_patterns=tuple(HOST_AGNOSTIC_REGISTRY) + tuple(PI_MANIFEST_REGISTRY),
    posture_manifest_collectors=(collect_pi_mcp_manifests, no_manifests),
    installed_posture_collectors=(collect_pi_endpoint_mcp_manifests, no_manifests),
    extra_installed_posture_collectors={
        project_trust.RULE_ID: collect_pi_project_trust_manifests,
    },
)
