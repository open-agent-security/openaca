from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from tools.component_ref import ComponentRef

# "target" | "plugin" | "skill" | "mcp_server" | "hook" | "command" | "agent" | "package"
NodeKind = str


@dataclass(frozen=True)
class Node:
    key: str  # occurrence identity (ADR-0031); never the purl.
    # V1 invariant: this IS the CycloneDX bom-ref.
    kind: NodeKind
    ref: Optional[ComponentRef]  # None only for the synthetic target root


@dataclass(frozen=True)
class Edge:
    parent: str  # parent node key
    child: str  # child node key


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge] = field(default_factory=list)

    @property
    def root(self) -> Node:
        roots = [n for n in self.nodes.values() if n.kind == "target"]
        if len(roots) != 1:
            raise ValueError(f"graph must have exactly one target root, found {len(roots)}")
        return roots[0]

    def _parent_of(self) -> dict[str, str]:
        return {e.child: e.parent for e in self.edges}

    def children_of(self, node: Node) -> list[Node]:
        return [self.nodes[e.child] for e in self.edges if e.parent == node.key]
