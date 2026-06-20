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

import json
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, Node
from tools.identity import canonical_component_identity
from tools.parsers import claude_plugin, claude_skill, package_json, pyproject_toml
from tools.parsers.claude_plugin_root import resolve_within
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec

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

    Nested project skills (`.claude/skills/<name>/SKILL.md` at any depth) and
    plugin custom skill-dir paths (the manifest's `"skills"` field) are handled
    here (Task 2.3). Endpoint mode is Task 2.4.
    """
    if parent.kind == "target":
        plugin_manifest = directory / ".claude-plugin" / "plugin.json"
        plugin_root: Path | None = None
        if plugin_manifest.is_file():
            _descend_into_plugin(graph, parent, directory, plugin_manifest)
            plugin_root = directory
        _add_project_skills(graph, parent, directory, exclude_under=plugin_root)
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


def _add_project_skills(
    graph: Graph, parent: Node, directory: Path, exclude_under: Path | None = None
) -> None:
    """Project skills live at `.claude/skills/<name>/SKILL.md` at ANY depth.

    Discovery uses the same gitignore-aware tree walk as `parse_repo_grouped`
    so we skip `node_modules/`, `.git/`, and gitignored dirs. Each skill dir
    becomes a `skill` child of `parent` (the target).

    `exclude_under` is the root of a plugin already descended from `parent`:
    skills inside that subtree belong to the plugin branch (single-parent
    invariant), so they are skipped here to avoid double-discovery.
    """
    spec = load_gitignore_spec(directory)
    exclude_resolved = exclude_under.resolve() if exclude_under is not None else None
    for path in iter_unignored_files(directory, spec):
        if path.name != "SKILL.md" or not _is_project_skill_md(path, directory):
            continue
        if exclude_resolved is not None and path.resolve().is_relative_to(exclude_resolved):
            continue
        _add_skill_node(graph, parent, path.parent)


def _is_project_skill_md(path: Path, root: Path) -> bool:
    """True iff `path` is `.../.claude/skills/<name>/SKILL.md` relative to root."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    # parts == (..., ".claude", "skills", "<name>", "SKILL.md")
    return (
        len(parts) >= 4
        and parts[-1] == "SKILL.md"
        and parts[-3] == "skills"
        and parts[-4] == ".claude"
    )


def _add_bundled_skills(graph: Graph, parent: Node, directory: Path) -> None:
    """Plugin-bundled skills live at `<plugin-root>/skills/<name>/SKILL.md`,
    or at a custom directory named by the manifest's `"skills"` field.

    Path resolution mirrors `claude_plugin_root._parse_bundled_skills`:
    `resolve_within` rejects traversal outside the plugin root, the default
    `skills/` is always tried, and a custom dir equal to the default is
    deduped.
    """
    skill_dirs: list[Path] = []
    default_skills = resolve_within(directory, "skills")
    if default_skills is not None and default_skills.is_dir():
        skill_dirs.append(default_skills)
    custom_skills = _plugin_custom_skills_field(directory)
    if isinstance(custom_skills, str):
        custom_dir = resolve_within(directory, custom_skills)
        if custom_dir is not None and custom_dir.is_dir():
            skill_dirs.append(custom_dir)

    seen_dirs: set[Path] = set()
    for skills_dir in skill_dirs:
        resolved = skills_dir.resolve()
        if resolved in seen_dirs:
            continue
        seen_dirs.add(resolved)
        _add_skills_from_dir(graph, parent, skills_dir)


def _plugin_custom_skills_field(plugin_root: Path) -> object:
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("skills")


def _add_skills_from_dir(graph: Graph, parent: Node, skills_dir: Path) -> None:
    if not skills_dir.is_dir():
        return
    for skill_subdir in sorted(skills_dir.iterdir()):
        if (skill_subdir / "SKILL.md").is_file():
            _add_skill_node(graph, parent, skill_subdir)


def _add_skill_node(graph: Graph, parent: Node, skill_subdir: Path) -> None:
    skill_md = skill_subdir / "SKILL.md"
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
