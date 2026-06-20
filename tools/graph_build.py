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
from tools.parsers import (
    claude_install,
    claude_plugin,
    claude_skill,
    mcp_json,
    package_json,
    pyproject_toml,
)
from tools.parsers.claude_plugin_root import resolve_within
from tools.parsers.gitignore import iter_unignored_files, load_gitignore_spec
from tools.parsers.settings_layers import SCOPE_PRECEDENCE
from tools.parsers.settings_layers import load as load_settings

# Top-level dependency manifests handled in repo mode. Each maps a filename to
# the leaf parser that emits its package refs. Task 2.2+ extends descent with
# the agent-component surfaces (plugins, skills, MCP, settings).
_DEP_MANIFEST_PARSERS = {
    "package.json": package_json.parse,
    "pyproject.toml": pyproject_toml.parse,
}

_TARGET_KEY = "openaca:target"


def _add_child(graph: Graph, parent_node: Node, child_node: Node) -> Node:
    """Insert `child_node` under `parent_node`, deduping both node and edge.

    Spec construction step 5 (the safety net): the `nodes` dict already dedups
    by key, but `edges` is a list that never dedups. When two discovery paths
    reach the SAME occurrence (same key) from the SAME parent, appending the
    edge unconditionally leaves a duplicate that trips `Graph.validate()`'s
    multiple-parents check. Route every node+edge creation through here so the
    edge is added at most once. A same occurrence reaching two DIFFERENT
    parents still (correctly) trips validate — that's a real placement bug.

    Returns the canonical node for the key (the pre-existing one if present).
    """
    existing = graph.nodes.get(child_node.key)
    if existing is None:
        graph.nodes[child_node.key] = child_node
    edge = Edge(parent=parent_node.key, child=child_node.key)
    if edge not in graph.edges:
        graph.edges.append(edge)
    return graph.nodes[child_node.key]


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


def build_graph(target: Path, mode: str, project_root: Path | None = None) -> Graph:
    if mode not in ("repo", "endpoint"):
        raise ValueError(f"unknown mode: {mode!r}")

    root = Node(key=_TARGET_KEY, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    if mode == "endpoint":
        _seed_endpoint(graph, root, Path(target), project_root)
    else:
        descend(graph, root, Path(target))
    graph.validate()
    return graph


def _seed_endpoint(
    graph: Graph, target: Node, install_root: Path, project_root: Path | None
) -> None:
    """Endpoint mode: the target's children are seeded from resolved Claude
    config, not a filesystem glob. Recursive descent (the SAME `descend` used
    in repo mode) still applies under each seeded root.

    Three seed surfaces:

    - **Active plugins** (`installed_plugins.json` ∩ settings `enabledPlugins`):
      each becomes a `plugin` child of the target. We then `descend` into the
      plugin's on-disk install path (reusing the repo-mode plugin branch, which
      walks bundled `skills/<name>/` and their dep manifests), and attach the
      plugin's own tier-2 lockfile deps as `package` children of the plugin.
    - **Project skills** under `<project_root>/.claude/skills/...`: reuse the
      repo-mode project-skill discovery as `skill` children of the target.
    - **Remote MCPs** declared in settings `mcpServers` (URLs/commands, nothing
      on disk): `mcp_server` leaf children of the target, no descent.
    """
    layers = load_settings(install_root, project_root=project_root)
    effective = layers.merged("endpoint")
    by_scope = layers.by_scope()

    plugins_map, lockfile_path, _ = claude_install._load_plugins_map(install_root)
    enabled = effective.get("enabledPlugins") or {}
    if isinstance(enabled, dict) and plugins_map is not None and lockfile_path is not None:
        _seed_active_plugins(graph, target, enabled, plugins_map, lockfile_path, layers)

    if project_root is not None:
        _add_project_skills(graph, target, project_root)

    _seed_remote_mcps(graph, target, install_root, project_root, by_scope)


def _seed_active_plugins(
    graph: Graph,
    target: Node,
    enabled: dict,
    plugins_map: dict,
    lockfile_path: Path,
    layers,
) -> None:
    for plugin_key, is_enabled in enabled.items():
        if is_enabled is not True:
            continue
        raw_entries = plugins_map.get(plugin_key)
        if not isinstance(raw_entries, list) or not raw_entries:
            continue
        entries = [(i, e) for i, e in enumerate(raw_entries) if isinstance(e, dict)]
        if not entries:
            continue
        scope = claude_install._enabling_scope(plugin_key, layers, "endpoint")
        entry, index, _ = claude_install._select_install_entry(entries, scope)

        plugin_name, marketplace = claude_install._split_plugin_key(plugin_key)
        version = entry.get("version")
        if version is not None and not isinstance(version, str):
            continue
        component_identity = claude_install._plugin_identity(plugin_name, marketplace)
        attributed_id = f"{component_identity}@{version}" if version else component_identity

        self_ref = ComponentRef(
            name=plugin_name,
            version=version,
            component_identity=component_identity,
            source_manifest=str(lockfile_path),
            source_locator=f"$.plugins.{plugin_key}[{index}]",
            extra={"component_type": "plugin"},
        )
        plugin_node = Node(key=occurrence_key(self_ref), kind="plugin", ref=self_ref)
        _add_child(graph, target, plugin_node)

        install_path = entry.get("installPath")
        if isinstance(install_path, str) and install_path:
            # Reuse the repo-mode plugin descent for bundled skills + their deps.
            descend(graph, plugin_node, Path(install_path))
            # Plugin tier-2 lockfile deps: parity with parse_install — attach as
            # package children of the plugin node (NOT a skill).
            for ref in claude_install._walk_plugin_implementation_deps(
                Path(install_path), attributed_to=attributed_id
            ):
                node = Node(key=occurrence_key(ref), kind="package", ref=ref)
                _add_child(graph, plugin_node, node)


def _seed_remote_mcps(
    graph: Graph,
    target: Node,
    install_root: Path,
    project_root: Path | None,
    by_scope: dict,
) -> None:
    scope_to_settings_path = {
        "user": install_root / "settings.json",
        "project": (project_root / ".claude" / "settings.json")
        if project_root is not None
        else None,
        "local": (project_root / ".claude" / "settings.local.json")
        if project_root is not None
        else None,
    }
    for scope in SCOPE_PRECEDENCE:
        if scope == "managed":
            continue
        settings_path = scope_to_settings_path.get(scope)
        if settings_path is None:
            continue
        scope_data = by_scope.get(scope) or {}
        mcp_servers = scope_data.get("mcpServers")
        if not isinstance(mcp_servers, dict):
            continue
        for ref in mcp_json.parse_mcp_servers(
            mcp_servers,
            source_manifest=str(settings_path),
            locator_prefix="$.mcpServers (inlined)",
        ):
            if _component_type(ref) != "mcp_server":
                continue
            node = Node(key=occurrence_key(ref), kind="mcp_server", ref=ref)
            _add_child(graph, target, node)


def descend(graph: Graph, parent: Node, directory: Path) -> None:
    """Discover children of `parent` under `directory` and recurse.

    Parentage is by construction: a child's parent is `parent` because we
    descended into it from `parent`. The discovery surface depends on the
    parent's kind:

    - `target`: a `.claude-plugin/plugin.json` at ANY depth makes its dir a
      plugin root (→ plugin child, descended *as a plugin*); project skills
      (`.claude/skills/<name>/SKILL.md`) become skill children; bare
      dependency manifests become `package` children (software-dependency).
      Plugin subtrees are excluded from the project-skill walk (single-parent).
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
        # Plugins are discovered at ANY depth (parity with parse_repo, which
        # matches `.claude-plugin/plugin.json` anywhere in the tree). Each plugin
        # root is a boundary handoff: the plugin owns its entire subtree, so its
        # bundled skills/deps hang off the plugin node, never off the target
        # (single-parent invariant).
        plugin_roots = _find_plugin_roots(directory)
        for plugin_root in plugin_roots:
            _descend_into_plugin(
                graph, parent, plugin_root, plugin_root / ".claude-plugin" / "plugin.json"
            )
        _add_project_skills(graph, parent, directory, exclude_under=plugin_roots)
        # A plugin root owns its own dep manifests (emitted under the plugin via
        # the plugin-branch descent); emitting them again under target would
        # double-parent the same occurrence and trip validate(). The target's
        # bare-dep walk is non-recursive (only `directory/`), so it only needs to
        # skip when `directory` itself is a plugin root.
        if not any(_same_path(directory, root) for root in plugin_roots):
            _add_dep_manifest_packages(graph, parent, directory)
    elif parent.kind == "plugin":
        _add_bundled_skills(graph, parent, directory)
        _add_dep_manifest_packages(graph, parent, directory)
    elif parent.kind == "skill":
        _add_dep_manifest_packages(graph, parent, directory)


def _same_path(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


def _find_plugin_roots(directory: Path) -> list[Path]:
    """Plugin roots are dirs containing `.claude-plugin/plugin.json`, at ANY
    depth (parity with parse_repo). Discovery uses the same gitignore-aware walk
    as project-skill discovery so we skip `node_modules/`, `.git/`, gitignored
    dirs. Returns each plugin root sorted for determinism.
    """
    spec = load_gitignore_spec(directory)
    roots: list[Path] = []
    seen: set[Path] = set()
    for path in iter_unignored_files(directory, spec):
        if path.name != "plugin.json" or path.parent.name != ".claude-plugin":
            continue
        root = path.parent.parent
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(root)
    return sorted(roots)


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
    _add_child(graph, target, plugin_node)
    descend(graph, plugin_node, plugin_root)


def _add_project_skills(
    graph: Graph, parent: Node, directory: Path, exclude_under: list[Path] | None = None
) -> None:
    """Project skills live at `.claude/skills/<name>/SKILL.md` at ANY depth.

    Discovery uses the same gitignore-aware tree walk as `parse_repo_grouped`
    so we skip `node_modules/`, `.git/`, and gitignored dirs. Each skill dir
    becomes a `skill` child of `parent` (the target). Symlinked directories are
    not followed (matches the current scanner; tracked separately).

    `exclude_under` is the set of plugin roots already descended from `parent`:
    skills inside any of those subtrees belong to the plugin branch
    (single-parent invariant), so they are skipped here to avoid double-discovery.
    """
    spec = load_gitignore_spec(directory)
    exclude_resolved = [p.resolve() for p in exclude_under] if exclude_under else []
    for path in iter_unignored_files(directory, spec):
        if path.name != "SKILL.md" or not _is_project_skill_md(path, directory):
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
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
        if skill_subdir.name.startswith("."):  # skip .DS_Store, .git, etc.
            continue
        if (skill_subdir / "SKILL.md").is_file():
            _add_skill_node(graph, parent, skill_subdir)


def _add_skill_node(graph: Graph, parent: Node, skill_subdir: Path) -> None:
    skill_md = skill_subdir / "SKILL.md"
    for ref in claude_skill.parse(skill_md):
        skill_node = Node(key=occurrence_key(ref), kind="skill", ref=ref)
        _add_child(graph, parent, skill_node)
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
            _add_child(graph, parent, node)
