from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Optional

from tools.component_ref import ComponentRef

# "target" | "plugin" | "skill" | "mcp_server" | "hook" | "command" | "agent" | "package"
NodeKind = str


class GraphInvariantError(Exception):
    pass


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

    def validate(self) -> None:
        targets = [n for n in self.nodes.values() if n.kind == "target"]
        if len(targets) != 1:
            raise GraphInvariantError(f"expected exactly one target, found {len(targets)}")
        target_key = targets[0].key
        parents: dict[str, str] = {}
        for e in self.edges:
            if e.parent not in self.nodes or e.child not in self.nodes:
                raise GraphInvariantError(f"edge endpoint missing: {e}")
            if e.child in parents:
                raise GraphInvariantError(f"node {e.child} has multiple parents")
            parents[e.child] = e.parent
        for key in self.nodes:
            seen, cur = set(), key
            while cur in parents:
                if cur in seen:
                    raise GraphInvariantError(f"cycle detected through {cur}")
                seen.add(cur)
                cur = parents[cur]
            if cur != target_key:
                raise GraphInvariantError(f"node {key} is not connected to the target root")

    def children_of(self, node: Node) -> list[Node]:
        return [self.nodes[e.child] for e in self.edges if e.parent == node.key]

    def lineage(self, node: Node) -> list[Node]:
        """node → ... → target root, inclusive."""
        parent_of = self._parent_of()
        chain, seen, cur = [node], {node.key}, node.key
        while cur in parent_of:
            cur = parent_of[cur]
            if cur in seen:
                raise GraphInvariantError(f"cycle detected through {cur}")
            if cur not in self.nodes:
                raise GraphInvariantError(f"dangling parent reference to {cur!r}")
            seen.add(cur)
            chain.append(self.nodes[cur])
        return chain

    _AGENT_KINDS: ClassVar[frozenset[str]] = frozenset(
        {"plugin", "skill", "mcp_server", "hook", "command", "agent"}
    )

    def scope_of(self, node: Node) -> str:
        """package nodes only: agent-dependency iff an agent component is an
        ancestor before the target root; else software-dependency."""
        if node.kind != "package":
            return "agent-component"
        for anc in self.lineage(node)[1:]:  # exclude self
            if anc.kind == "target":
                break
            if anc.kind in self._AGENT_KINDS:
                return "agent-dependency"
        return "software-dependency"

    def nearest_plugin_ancestor(self, node: Node) -> Optional[Node]:
        for anc in self.lineage(node)[1:]:
            if anc.kind == "plugin":
                return anc
        return None

    def attribution_for(self, node: Node) -> Optional[str]:
        """The "via plugin X" attribution string for a node, or None.

        The nearest plugin ancestor's component_identity, versioned
        (`<identity>@<version>`) when the plugin carries a version — reproducing
        the pre-graph `attributed_to` value. A plugin attributes to None (it has
        no plugin ancestor). Pure derivation over the edges; the single source
        for both scan (ref projection) and bom."""
        plugin = self.nearest_plugin_ancestor(node)
        if plugin is None or plugin.ref is None:
            return None
        identity = plugin.ref.component_identity
        if not identity:
            return None
        return f"{identity}@{plugin.ref.version}" if plugin.ref.version else identity
