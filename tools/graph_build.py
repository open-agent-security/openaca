"""Construct the composition graph by recursive descent over a target.

`build_graph(target, mode)` walks the target and produces a `Graph` whose
edges encode parentage: the synthetic target root, then a child node per
discovered component or package, recursing into each.

`graph_build` owns *placement* (which parent a node hangs from); the leaf
parsers in `tools.parsers` own *content* (what a manifest declares). Scope is
never stamped on the ref here — it is derived from the graph (`Graph.scope_of`).

Node identity is the *occurrence* (ADR-0031), never the bare purl: two
manifests declaring the same purl yield two distinct package nodes. The target
root's key is a fixed logical value (`openaca:target`) so repo BOMs are
reproducible across machines — the resolved scan path is evidence, not identity.
"""

from __future__ import annotations

from pathlib import Path

from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, Node
from tools.identity import canonical_component_identity
from tools.parsers import package_json, pyproject_toml

# Top-level dependency manifests handled in repo mode. Each maps a filename to
# the leaf parser that emits its package refs. Task 2.2+ extends descent with
# the agent-component surfaces (plugins, skills, MCP, settings).
_DEP_MANIFEST_PARSERS = {
    "package.json": package_json.parse,
    "pyproject.toml": pyproject_toml.parse,
}

_TARGET_KEY = "openaca:target"


def occurrence_key(ref: ComponentRef) -> str:
    """The node key for a ref: its occurrence identity, never the bare purl.

    Components use their canonical component identity. Packages use the
    composite of where they were declared (source_manifest + source_locator)
    and what they are (purl or name), so two manifests declaring the same purl
    yield distinct keys.
    """
    component_type = (ref.extra or {}).get("component_type")
    if component_type and component_type != "package":
        identity = canonical_component_identity(ref)
        if identity:
            return identity
    what = ref.purl or ref.name or ""
    return f"{ref.source_manifest}#{ref.source_locator}#{what}"


def build_graph(target: Path, mode: str) -> Graph:
    if mode == "endpoint":
        raise NotImplementedError("endpoint mode lands in Task 2.4")
    if mode != "repo":
        raise ValueError(f"unknown mode: {mode!r}")

    root = Node(key=_TARGET_KEY, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    descend(graph, root, Path(target))
    graph.validate()
    return graph


def descend(graph: Graph, parent: Node, directory: Path) -> None:
    """Discover children of `parent` under `directory` and recurse.

    Task 2.1 handles only top-level dependency manifests (the bare-package
    case). Task 2.2+ adds agent-component boundary descent (plugins, skills,
    MCP servers) here: each discovered component becomes a child node and
    `descend` recurses into its directory with that node as the new parent.
    """
    _add_dep_manifest_packages(graph, parent, directory)


def _add_dep_manifest_packages(graph: Graph, parent: Node, directory: Path) -> None:
    for filename, parse in _DEP_MANIFEST_PARSERS.items():
        manifest = directory / filename
        if not manifest.is_file():
            continue
        for ref in parse(manifest):
            node = Node(key=occurrence_key(ref), kind="package", ref=ref)
            graph.nodes[node.key] = node
            graph.edges.append(Edge(parent=parent.key, child=node.key))
