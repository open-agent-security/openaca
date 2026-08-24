"""A test-only kind. Nothing shipping returns more than one agent, so the
multi-document paths are only reachable through this.

It declares itself through the same registry API a real kind uses; a change that
re-specialises a path for Claude Code therefore breaks it.
"""

from __future__ import annotations

from pathlib import Path

import tools.agent_kinds as agent_kinds
from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext
from tools.graph import Graph, Node

SYNTHETIC_ID = "synthetic"


def register_synthetic_kind(monkeypatch, *, agent_ids: list[str]) -> AgentKind:
    def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
        return [
            AgentInstance(
                kind_id=SYNTHETIC_ID,
                display_name=f"Synthetic {agent_id}",
                source=ctx.source,
                root_label=SYNTHETIC_ID,
                coverage_baseline="partial",
                config_root=ctx.config_dir or Path("."),
                scan_root=ctx.scan_root,
                agent_id=agent_id,
            )
            for agent_id in agent_ids
        ]

    def compose(agent, *, include_gitignored=False, warnings=None) -> Graph:
        root = Node(key=agent.bom_ref, kind="target", ref=None)
        return Graph(nodes={root.key: root})

    kind = AgentKind(
        id=SYNTHETIC_ID,
        display_name="Synthetic",
        cardinality="many_per_place",
        root_label=SYNTHETIC_ID,
        coverage_baseline={"installed": "partial", "declared": "partial"},
        discover=discover,
        compose=compose,
    )
    monkeypatch.setattr(agent_kinds, "REGISTRY", (kind,))
    return kind
