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
    key: str  # occurrence key / CycloneDX bom-ref (ADR-0042).
    # V1 invariant: this IS the CycloneDX bom-ref.
    kind: NodeKind
    ref: Optional[ComponentRef]  # None only for the synthetic target root


@dataclass(frozen=True)
class Edge:
    parent: str  # parent node key
    child: str  # child node key


def ref_occurrence_key(ref: ComponentRef) -> tuple[str, ...]:
    """Content key mapping a (possibly `dataclasses.replace`d) ref back to its
    graph `Node`.

    Scan projects the flat ref list as `dataclasses.replace(node.ref,
    scope=...)` copies, so the occurrence-identifying fields are untouched.
    Keying on those fields (manifest + locator + source/display facts) lets any
    output site (render, sarif, finding_output) recover a
    ref's node without recomputing the build-time occurrence key (which needs
    the source normalizer those sites do not have). Shared so every consumer
    maps a `ComponentRef` to its `Node` identically."""
    return (
        str(ref.source_manifest or ""),
        ref.source_locator or "",
        ref.ecosystem or "",
        ref.name or "",
        ref.version or "",
        ref.component_identity or "",
    )


class WarningLog(list):
    """Warnings, plus the subset meaning "a component may be missing".

    `openaca:composition_coverage` qualifies the COMPONENT graph, so only that
    subset should lower it: a note about a component we *did* read — its
    marketplace is unregistered, its enable value was malformed and defaulted —
    says nothing about whether we identified it.

    A `list` subclass rather than a new type, so it passes unchanged through
    every existing `warnings: list[str]` parameter and every `append`,
    `extend`, and `len` already written against it.

    Recording a gap is **opt-in**: a plain `append` is a note. That keeps
    `complete` reachable and stops a newly added diagnostic from silently
    degrading a kind's coverage. The cost is that a genuine gap must remember
    `gap()`, which is why the converted sites are the well-known
    "could not parse / unavailable / missing from" family.
    """

    def __init__(self, iterable=()) -> None:
        super().__init__(iterable)
        self.gaps: list[str] = []

    def gap(self, message: str) -> None:
        self.append(message)
        self.gaps.append(message)

    def absorb(self, other: "list[str]") -> None:
        """Extend with `other`, carrying its gaps across if it has any."""
        self.extend(other)
        self.gaps.extend(getattr(other, "gaps", []))


def record_gap(warnings: "list[str] | None", message: str) -> None:
    """Record a component gap on `warnings`, whatever kind of list it is.

    Sites deep in the parsers receive a plain `list[str]` and have no `Graph`
    in scope. This lets them classify without threading one through: a
    `WarningLog` records the gap, a plain list degrades to a note.
    """
    if warnings is None:
        return
    if isinstance(warnings, WarningLog):
        warnings.gap(message)
    else:
        warnings.append(message)


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge] = field(default_factory=list)
    warnings: WarningLog = field(default_factory=WarningLog, repr=False)

    def record_gap(self, message: str) -> None:
        """A warning that also means a component may be missing."""
        self.warnings.gap(message)

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

        Uses the nearest plugin ancestor's stable identity when available and
        its display name otherwise, adding the observed version when present.
        A node with no plugin ancestor attributes to None. This is presentation
        derived from edges, not a cross-BOM join key."""
        plugin = self.nearest_plugin_ancestor(node)
        if plugin is None or plugin.ref is None:
            return None
        identity = plugin.ref.component_identity
        if not identity and plugin.ref.name:
            identity = f"plugin/{plugin.ref.name}"
        if not identity:
            return None
        return f"{identity}@{plugin.ref.version}" if plugin.ref.version else identity

    def _node_by_ref_key(self) -> dict[tuple[str, ...], Node]:
        return {ref_occurrence_key(n.ref): n for n in self.nodes.values() if n.ref is not None}

    def node_for_ref(self, ref: ComponentRef) -> Optional[Node]:
        """Map an output-time `ComponentRef` (a finding's component) back to its
        graph `Node` by its stamped BOM reference when present, then by
        occurrence key. None when the ref has no matching node (e.g. a
        flat-BOM ref scanned without a reconstructable graph)."""
        bom_ref = ref.extra.get("bom_ref")
        if isinstance(bom_ref, str):
            node = self.nodes.get(bom_ref)
            if node is not None:
                return node
        return self._node_by_ref_key().get(ref_occurrence_key(ref))

    def attribution_for_ref(self, ref: ComponentRef) -> Optional[str]:
        """`attribution_for` keyed by a ref rather than a node; None when the ref
        does not map to a node in this graph."""
        node = self.node_for_ref(ref)
        return self.attribution_for(node) if node is not None else None
