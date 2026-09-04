"""Collect one installed agent's composition, posture findings and observations.

This is the collection half of what OpenACA's own hosted-service client used to
do inline. It is not upload-specific and it was not the client's — it is
OpenACA's, and it was moved down here first so that removing the client could
not take it along. What did not come down with it, and left with the client:
install-source trimming, the payload-vocabulary mapping, the filter that held
some rules back from an upload, and the exit-code carrier that wrapped
`ScannerUnavailable`.

`openaca/core/collect.py` re-exports the public surface (ADR-0028).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import click

from tools.agent_kinds import (
    AgentInstance,
    DiscoveryContext,
    build_agent_graph,
    discover_agents,
    kind_for,
    resolve_coverage,
)
from tools.bom import build_agent_bom
from tools.component_ref import ComponentRef
from tools.graph import Graph, WarningLog
from tools.observations import (
    ObservationFinding,
    SkillSpectorCommandNotFound,
    collect_skill_observations,
    collect_skillspector_findings,
)
from tools.posture import PostureFinding, run_posture_rules
from tools.posture.agent_surface import agent_extra_posture_manifests, agent_posture_manifests
from tools.scan import _component_gap_count, _count_active_plugins

__all__ = [
    "CollectedAgent",
    "ScannerUnavailable",
    "collect_for_agent",
    "collect_installed_agents",
]

_AGENT_SCOPES = frozenset({"agent-component", "agent-dependency"})


class ScannerUnavailable(Exception):
    """An `external_scanners` entry names a scanner whose command is absent.

    The one failure a `collect_*` call has that a caller is expected to handle,
    which is why it is published rather than left as a bare `Exception`. It is
    generic rather than SkillSpector-specific because `external_scanners` is a
    scanner-agnostic argument: a second scanner reuses this name instead of
    publishing a second one, and `SkillSpectorCommandNotFound` stays internal.

    It carries no `exit_code`. An exit code is a process's concern, not a
    library's; a caller that needs one supplies its own.
    """


@dataclass(frozen=True)
class CollectedAgent:
    """What one installed agent is composed of, and what is wrong with it.

    `config_root` is *this agent's own* configuration root, not the `config_dir`
    argument. On a machine running one kind the two agree; on a machine running
    two, the argument can be at most one kind's root, so a consumer relativising
    the other kind's paths against it silently produces bare basenames rather
    than an error. The root that is correct for every agent therefore travels
    with the result that needs it.
    """

    agent_kind: str
    agent_id: str | None
    config_root: Path
    bom: dict[str, Any]
    posture_findings: tuple[PostureFinding, ...]
    observations: tuple[ObservationFinding, ...]
    component_count: int
    warnings: tuple[str, ...]


def collect_installed_agents(
    *,
    config_dir: Path | None = None,
    project: Path | None = None,
    kind_id: str | None = None,
    external_scanners: tuple[str, ...] = (),
    include_target: bool = True,
) -> list[CollectedAgent]:
    """One result per installed agent, in discovery order.

    `source` is not a parameter: this function answers *what is installed
    here*, and a declared (repo) composition is `openaca bom repo`'s question.

    Discovering nothing returns an empty list rather than raising — "nothing is
    installed here" is an answer.

    Raises `ScannerUnavailable` when `external_scanners` names a scanner whose
    command is not installed.
    """
    agents = discover_agents(
        DiscoveryContext(
            source="installed", config_dir=config_dir, project_root=project, kind_id=kind_id
        )
    )
    return [
        collect_for_agent(agent, external_scanners=external_scanners, include_target=include_target)
        for agent in agents
    ]


def collect_for_agent(
    agent: AgentInstance,
    *,
    external_scanners: tuple[str, ...] = (),
    include_target: bool = True,
) -> CollectedAgent:
    """Collect one already-discovered agent."""
    if agent.config_root is None:
        # An impossible invariant rather than an operational failure: installed
        # discovery always sets `config_root`. `AgentInstance` nonetheless
        # permits `None`, and letting one through would reach `build_agent_bom`
        # as the string "None". `ValueError` deliberately, not a domain error —
        # this guards against a bug in a kind module, and must not become a
        # second public error on a surface fixed at one.
        raise ValueError(f"agent kind {agent.kind_id!r} resolved no config_root")
    warnings: WarningLog = WarningLog()
    graph, refs = _agent_refs(agent, warnings)
    bom = build_agent_bom(
        refs,
        # The caller decides whether the document names a place. A consumer
        # keeping the BOM locally wants it; a consumer shipping it elsewhere
        # must not carry an absolute path off the machine.
        target=str(agent.config_root) if include_target else None,
        # Agent-scope refs, where the scan path passes all refs to the same
        # counter. Not reconciled here: matching the scan path would change the
        # source-unit count in the document this function returns. A scan-side
        # change owns it.
        source_unit_count=_count_active_plugins(refs),
        source_unit_label="active plugin",
        graph=graph,
        agent_kind=agent.kind_id,
        agent_id=agent.agent_id,
        agent_name=agent.display_name,
        composition_source=agent.source,
        composition_coverage=resolve_coverage(
            agent.coverage_baseline, evidence_gaps=_component_gap_count(warnings)
        ),
    ).to_cyclonedx()
    mcp_manifests, settings_manifests = agent_posture_manifests(agent, refs)
    extra_manifests = agent_extra_posture_manifests(agent, refs)
    posture_findings = list(
        run_posture_rules(
            refs,
            mcp_manifests,
            settings_manifests,
            allowed_rules=kind_for(agent.kind_id).posture_rules,
            extra_manifests=extra_manifests,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
        )
    )
    observations, scanner_posture = _collect_scanner_findings(
        refs, external_scanners=external_scanners
    )
    posture_findings.extend(
        replace(f, agent_kind=agent.kind_id, agent_id=agent.agent_id) for f in scanner_posture
    )
    return CollectedAgent(
        agent_kind=agent.kind_id,
        agent_id=agent.agent_id,
        config_root=agent.config_root,
        bom=bom,
        posture_findings=tuple(posture_findings),
        observations=tuple(
            replace(o, agent_kind=agent.kind_id, agent_id=agent.agent_id) for o in observations
        ),
        component_count=len(bom.get("components") or []),
        warnings=tuple(warnings),
    )


def _agent_refs(agent: AgentInstance, warnings: list[str]) -> tuple[Graph, list[ComponentRef]]:
    """Build the agent's composition graph and return its agent-scope refs.

    `warnings` is populated in place by `build_agent_graph` (malformed or
    unreadable manifests, invalid install entries) — the same signal the scan
    path feeds into `resolve_coverage(..., evidence_gaps=len(warnings))` so a
    partially-composed agent is not reported as `complete`.

    Isolated as a helper so tests can monkeypatch this single boundary
    rather than every graph-build internal.
    """
    graph = build_agent_graph(agent, warnings=warnings)
    all_refs = [
        replace(
            node.ref,
            scope=graph.scope_of(node),
            extra={**(node.ref.extra or {}), "bom_ref": node.key},
        )
        for node in graph.nodes.values()
        if node.ref is not None
    ]
    return graph, [r for r in all_refs if r.scope in _AGENT_SCOPES]


def _collect_scanner_findings(
    refs: list[ComponentRef],
    *,
    external_scanners: tuple[str, ...],
) -> tuple[list[ObservationFinding], list[PostureFinding]]:
    observations = collect_skill_observations(refs)
    posture_findings: list[PostureFinding] = []
    if "nvidia-skillspector" in external_scanners:
        try:
            skillspector_findings = collect_skillspector_findings(refs)
        except SkillSpectorCommandNotFound as exc:
            raise ScannerUnavailable(str(exc)) from exc
        observations.extend(skillspector_findings.observations)
        posture_findings.extend(skillspector_findings.posture_findings)
        for warning in skillspector_findings.warnings:
            # Left on stderr where the code puts it. Routing it into
            # `CollectedAgent.warnings` instead would either change what a
            # caller reading that field reports — `warnings` also carries
            # malformed-manifest notes, which are not echoed today — or need a
            # second warnings channel. Deferred, plan 045.
            click.echo(f"warning: {warning}", err=True)
    return observations, posture_findings
