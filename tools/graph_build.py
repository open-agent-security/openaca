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
from tools.parsers import claude_plugin, claude_skill, package_json, pyproject_toml

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

    The key is the occurrence — where the ref was declared
    (source_manifest + source_locator) plus what it is — never the bare
    component identity or purl (spec: openaca:identity ≈ source path +
    locator + identity). So two same-named skills at different paths, or two
    manifests declaring the same purl, yield distinct nodes; a single
    occurrence reached by two discovery paths collapses (same manifest +
    locator + what).
    """
    component_type = (ref.extra or {}).get("component_type")
    if component_type and component_type != "package":
        what = canonical_component_identity(ref) or ref.name or ""
    else:
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

    Parentage is by construction: a child's parent is `parent` because we
    descended into it from `parent`. The discovery surface depends on the
    parent's kind:

    - `target`: a `.claude-plugin/plugin.json` here makes `directory` a
      plugin root (→ plugin child, descended *as a plugin*); project skills
      (`.claude/skills/<name>/SKILL.md`) become skill children; bare
      dependency manifests become `package` children (software-dependency).
    - `plugin`: bundled `skills/<name>/SKILL.md` become skill children, and
      the plugin's own dependency manifests become `package` children
      (its implementation deps).
    - `skill`: dependency manifests in the skill dir become `package`
      children (agent-dependency).

    Task 2.2 covers the three layouts above. Nested project skills, custom
    plugin skill-dir paths, and endpoint mode are Tasks 2.3/2.4.
    """
    if parent.kind == "target":
        plugin_manifest = directory / ".claude-plugin" / "plugin.json"
        if plugin_manifest.is_file():
            _descend_into_plugin(graph, parent, directory, plugin_manifest)
        else:
            _add_project_skills(graph, parent, directory)
        _add_dep_manifest_packages(graph, parent, directory)
    elif parent.kind == "plugin":
        _add_bundled_skills(graph, parent, directory)
        _add_dep_manifest_packages(graph, parent, directory)
    elif parent.kind == "skill":
        _add_dep_manifest_packages(graph, parent, directory)


def _descend_into_plugin(
    graph: Graph, target: Node, plugin_root: Path, plugin_manifest: Path
) -> None:
    """Create the plugin node (child of target) and descend into its subtree.

    Reuses `claude_plugin.parse` only to obtain the plugin self-identity ref;
    placement (the plugin → target edge, and which children hang off the
    plugin) is owned here.
    """
    self_ref = next(
        (r for r in claude_plugin.parse(plugin_manifest) if _component_type(r) == "plugin"),
        None,
    )
    if self_ref is None:
        return
    plugin_node = Node(key=occurrence_key(self_ref), kind="plugin", ref=self_ref)
    graph.nodes[plugin_node.key] = plugin_node
    graph.edges.append(Edge(parent=target.key, child=plugin_node.key))
    descend(graph, plugin_node, plugin_root)


def _add_project_skills(graph: Graph, parent: Node, directory: Path) -> None:
    """Project skills live at `<directory>/.claude/skills/<name>/SKILL.md`."""
    skills_dir = directory / ".claude" / "skills"
    _add_skills_from_dir(graph, parent, skills_dir)


def _add_bundled_skills(graph: Graph, parent: Node, directory: Path) -> None:
    """Plugin-bundled skills live at `<plugin-root>/skills/<name>/SKILL.md`."""
    skills_dir = directory / "skills"
    _add_skills_from_dir(graph, parent, skills_dir)


def _add_skills_from_dir(graph: Graph, parent: Node, skills_dir: Path) -> None:
    if not skills_dir.is_dir():
        return
    for skill_subdir in sorted(skills_dir.iterdir()):
        skill_md = skill_subdir / "SKILL.md"
        if not skill_md.is_file():
            continue
        for ref in claude_skill.parse(skill_md):
            skill_node = Node(key=occurrence_key(ref), kind="skill", ref=ref)
            graph.nodes[skill_node.key] = skill_node
            graph.edges.append(Edge(parent=parent.key, child=skill_node.key))
            descend(graph, skill_node, skill_subdir)


def _component_type(ref: ComponentRef) -> object:
    return (ref.extra or {}).get("component_type")


def _add_dep_manifest_packages(graph: Graph, parent: Node, directory: Path) -> None:
    for filename, parse in _DEP_MANIFEST_PARSERS.items():
        manifest = directory / filename
        if not manifest.is_file():
            continue
        for ref in parse(manifest):
            node = Node(key=occurrence_key(ref), kind="package", ref=ref)
            graph.nodes[node.key] = node
            graph.edges.append(Edge(parent=parent.key, child=node.key))
