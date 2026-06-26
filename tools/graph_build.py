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
import tomllib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from pathspec import GitIgnoreSpec

from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, Node
from tools.identity import canonical_component_identity
from tools.mcp_launch_resolve import normalize_pypi_name, resolve_mcp_launch_dir
from tools.parsers import (
    bun_lock,
    claude_command_agent,
    claude_install,
    claude_plugin,
    claude_settings,
    claude_skill,
    hooks_json,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    skill_lock,
    uv_lock,
)
from tools.parsers.claude_command_agent import Kind
from tools.parsers.claude_plugin_root import (
    _parse_bundled_command_agents,
    _parse_bundled_hooks,
    _parse_default_mcp,
    _parse_manifest_refs,
    resolve_within,
)
from tools.parsers.gitignore import is_ignored, iter_unignored_files, load_gitignore_spec
from tools.parsers.settings_layers import SCOPE_PRECEDENCE
from tools.parsers.settings_layers import load as load_settings

# Top-level dependency manifests handled in repo mode. Each maps a filename to
# the leaf parser that emits its package refs. Task 2.2+ extends descent with
# the agent-component surfaces (plugins, skills, MCP, settings).
_DEP_MANIFEST_PARSERS = {
    "package.json": package_json.parse,
    "pyproject.toml": pyproject_toml.parse,
    # Lockfiles fold into dep-manifest discovery: they emit transitive `package`
    # refs (`extra["transitive"]=True`) that dedup against manifest deps by
    # occurrence key. The endpoint plugin-own-deps path suppresses these via
    # `emit_own_root_deps=False` (the tier-2 lockfile walk owns them there).
    "package-lock.json": package_lock_json.parse,
    "uv.lock": uv_lock.parse,
    "bun.lock": bun_lock.parse,
}

_TARGET_KEY = "openaca:target"

# Directories that contain installed package closures rather than first-party
# source. `build_manifest_name_index` excludes manifests under these regardless
# of `include_gitignored`, preventing external `npx <pkg>` from matching an
# installed copy inside e.g. `node_modules/` and being mis-attributed as a
# local self-launch in endpoint mode (where the gitignore walk is disabled).
_NAME_INDEX_DEP_DIRS = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".virtualenv",
        ".tox",
        "site-packages",
        "__pycache__",
    }
)


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


# The path-normalizer threaded into every node-key construction. Takes a ref's
# absolute `source_manifest` and returns a machine-independent logical path.
SourceNormalizer = Callable[[str], str]


def _identity_normalizer(abs_path: str) -> str:
    return abs_path


def _make_normalizer(
    mode: str, target: Path, install_root: Path, project_root: Path | None
) -> SourceNormalizer:
    """Build the `source_manifest`-path normalizer for a scan.

    The node key's path portion must be a *stable logical path* (machine-specific
    root prefix stripped) so node keys — which become CycloneDX bom-refs — are
    reproducible across machines and dedup across them.

    - **repo mode**: the single scan `target` is the only root; the key path is
      `source_manifest` relative to `target` (POSIX), e.g.
      `.claude/skills/deploy/package.json`.
    - **endpoint mode**: paths span `install_root` (the scan target, e.g.
      `~/.claude`, incl. plugin install/cache dirs under it) and `project_root`
      (the project dir). Strip the matching known root and prefix a logical label
      so paths under different roots can't collide: `project/<rel>` under
      `project_root`, `endpoint/<rel>` under `install_root`. A path under neither
      falls back to the absolute path (last resort).
    """
    # Keep BOTH the logical (un-resolved) and resolved forms of each root.
    # Resolved roots make prefix-matching symlink-stable for genuinely-nested
    # paths (matches the `.resolve()` used elsewhere in descent). But a
    # project-local endpoint (e.g. a `.claude/skills/<name>` that is a SYMLINK
    # pointing OUTSIDE the project) carries a LOGICAL `source_manifest` under
    # `project_root`; resolving it follows the link out of the root and breaks
    # `relative_to`, falling back to a machine-specific absolute key. So
    # relativize the logical path against the logical root FIRST, and only fall
    # back to the resolved/resolved match.
    target_r = target.resolve()
    install_r = install_root.resolve()
    project_r = project_root.resolve() if project_root is not None else None

    def _rel(abs_path: str, root_logical: Path, root_resolved: Path) -> str | None:
        path = Path(abs_path)
        try:
            return path.relative_to(root_logical).as_posix()
        except ValueError:
            pass
        try:
            return path.resolve().relative_to(root_resolved).as_posix()
        except ValueError:
            return None

    if mode == "repo":

        def normalize(abs_path: str) -> str:
            rel = _rel(abs_path, target, target_r)
            return rel if rel is not None else abs_path

        return normalize

    def normalize(abs_path: str) -> str:
        # project_root first: when project_root is nested under install_root,
        # project files must keep their `project/` label rather than being
        # swallowed by the install-root branch.
        if project_root is not None and project_r is not None:
            rel = _rel(abs_path, project_root, project_r)
            if rel is not None:
                return f"project/{rel}"
        rel = _rel(abs_path, install_root, install_r)
        if rel is not None:
            return f"endpoint/{rel}"
        return abs_path

    return normalize


def occurrence_key(ref: ComponentRef, normalize: SourceNormalizer = _identity_normalizer) -> str:
    """The node key for a ref: its occurrence identity, never the bare purl.

    The key is the occurrence — where the ref was declared
    (source_manifest + source_locator) plus what it is — never the bare
    component identity or purl (spec: openaca:identity ≈ source path +
    locator + identity). So two same-named skills at different paths, or two
    manifests declaring the same purl, yield distinct nodes; a single
    occurrence reached by two discovery paths collapses (same manifest +
    locator + what).

    `normalize` maps the ref's absolute `source_manifest` to a stable logical
    path (machine root prefix stripped) so node keys are reproducible across
    machines. `ref.source_manifest` itself is left untouched (render still
    relativizes it for display); only the KEY is normalized.
    """
    component_type = (ref.extra or {}).get("component_type")
    if component_type and component_type != "package":
        what = canonical_component_identity(ref) or ref.name or ""
    else:
        what = ref.purl or ref.name or ""
    return f"{normalize(ref.source_manifest)}#{ref.source_locator}#{what}"


def build_graph(
    target: Path,
    mode: str,
    project_root: Path | None = None,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    if mode not in ("repo", "endpoint"):
        raise ValueError(f"unknown mode: {mode!r}")

    root = Node(key=_TARGET_KEY, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    # The node-key path normalizer (Stage 4): strips the machine-specific scan
    # root so node keys — which become CycloneDX bom-refs — are reproducible.
    # The gitignore root (`root_dir`/`root_spec`) and the normalize root derive
    # from the same scan root; they're separate concerns threaded in parallel.
    normalize = _make_normalizer(mode, Path(target), Path(target), project_root)
    # ADR-0039 launch resolution context, set per-branch below.
    attach_root_dir: Path | None = None
    attach_root_spec: GitIgnoreSpec | None = None
    attach_include_gitignored = include_gitignored
    if mode == "endpoint":
        _seed_endpoint(graph, root, Path(target), project_root, normalize, warnings=warnings)
        # Endpoint has no single repo root; installed artifacts are not
        # gitignore-filtered (parity with the descent's root_dir=None behavior).
        attach_include_gitignored = True
    else:
        # Repo mode honors the SCAN-ROOT `.gitignore` everywhere, matching
        # parse_repo_grouped: load the root spec ONCE and evaluate every
        # candidate path relative to the scan root, even inside nested
        # plugin/skill descents. Endpoint mode has no single repo root, so it
        # passes root_dir=None and helpers keep their per-directory behavior.
        root_dir = Path(target)
        root_spec = None if include_gitignored else load_gitignore_spec(root_dir)
        descend(
            graph,
            root,
            root_dir,
            normalize,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
        )
        attach_root_dir = root_dir
        attach_root_spec = root_spec
    name_index = build_manifest_name_index(
        Path(target), include_gitignored=attach_include_gitignored
    )
    if project_root is not None:
        # Endpoint mode: project_root is separate from install_root (target),
        # so its manifests are absent from the target walk. Merge them in so
        # that a project-scoped MCP declaring `npx <pkg>` can resolve by name
        # against the project's own package.json / pyproject.toml.
        # project_root entries take precedence over install_root entries.
        name_index = {
            **name_index,
            **build_manifest_name_index(project_root, include_gitignored=attach_include_gitignored),
        }
    _attach_mcp_launch_deps(
        graph,
        Path(target),
        normalize,
        name_index,
        project_root=project_root,
        include_gitignored=attach_include_gitignored,
        root_dir=attach_root_dir,
        root_spec=attach_root_spec,
    )
    graph.validate()
    return graph


def _seed_endpoint(
    graph: Graph,
    target: Node,
    install_root: Path,
    project_root: Path | None,
    normalize: SourceNormalizer,
    *,
    warnings: list[str] | None = None,
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
    - **Other direct components**: install-root direct skills
      (`<install_root>/skills/<name>/`), personal+project commands/agents
      (`commands/`, `agents/`, `.claude/commands|agents/`), and settings-scoped
      hooks. All children of the target (attribution None — direct, not
      plugin-bundled). See `_seed_direct_components`.
    """
    layers = load_settings(install_root, project_root=project_root)
    effective = layers.merged("endpoint")
    by_scope = layers.by_scope()

    plugins_map, lockfile_path, plugin_warnings = claude_install._load_plugins_map(install_root)
    if warnings is not None:
        warnings.extend(plugin_warnings)
    enabled = effective.get("enabledPlugins") or {}
    if isinstance(enabled, dict) and plugins_map is not None and lockfile_path is not None:
        _seed_active_plugins(
            graph,
            target,
            enabled,
            plugins_map,
            lockfile_path,
            layers,
            normalize,
            warnings=warnings,
        )

    if project_root is not None:
        # Project skills are the one endpoint surface the old _walk_project_skill_dirs
        # filtered by the project root's .gitignore (e.g. skills under an ignored
        # .worktrees/). Thread the project root as root_dir so that filtering is
        # preserved; installed-plugin/install-root surfaces stay unfiltered.
        project_skill_spec = load_gitignore_spec(project_root)
        _add_project_skills(
            graph,
            target,
            project_root,
            normalize=normalize,
            project_root=project_root,
            stamp_provenance=True,
            root_dir=project_root,
            root_spec=project_skill_spec,
        )
        # iterdir() follows symlinks; os.walk (used by iter_unignored_files) does
        # not. Call _add_skills_from_dir explicitly so symlinked skill dirs under
        # <project>/.claude/skills/ are discovered — parity with the old
        # _walk_project_skill_dirs path that called _walk_skill_dir (iterdir-based)
        # before iter_unignored_files. _add_child dedup collapses non-symlink dupes.
        # stamp_provenance matches _parse_direct_skill, which both old project-skill
        # walks shared.
        _add_skills_from_dir(
            graph,
            target,
            project_root / ".claude" / "skills",
            normalize=normalize,
            project_root=project_root,
            stamp_provenance=True,
        )

    _seed_remote_mcps(graph, target, install_root, project_root, by_scope, normalize)
    _seed_direct_components(graph, target, install_root, project_root, by_scope, normalize)


def _seed_active_plugins(
    graph: Graph,
    target: Node,
    enabled: dict,
    plugins_map: dict,
    lockfile_path: Path,
    layers,
    normalize: SourceNormalizer,
    *,
    warnings: list[str] | None = None,
) -> None:
    for plugin_key, is_enabled in enabled.items():
        if is_enabled is not True:
            continue
        raw_entries = plugins_map.get(plugin_key)
        if not isinstance(raw_entries, list) or not raw_entries:
            if warnings is not None:
                warnings.append(
                    f"plugin {plugin_key} enabled but missing from installed_plugins.json"
                )
            continue
        entries = [(i, e) for i, e in enumerate(raw_entries) if isinstance(e, dict)]
        if not entries:
            if warnings is not None:
                warnings.append(f"plugin {plugin_key}: no valid install entries; skipping")
            continue
        scope = claude_install._enabling_scope(plugin_key, layers, "endpoint")
        entry, index, warning = claude_install._select_install_entry(entries, scope)
        if warning is not None and warnings is not None:
            warnings.append(f"{plugin_key}: {warning}")

        plugin_name, marketplace = claude_install._split_plugin_key(plugin_key)
        version = entry.get("version")
        if version is not None and not isinstance(version, str):
            if warnings is not None:
                warnings.append(
                    f"{plugin_key}: non-string version {version!r} in "
                    "installed_plugins.json; skipping"
                )
            continue
        component_identity = claude_install._plugin_identity(plugin_name, marketplace)

        # Carry the same plugin metadata `parse_install` emitted so endpoint
        # renderers (gitCommitSha display, per-plugin tier-2 coverage) and
        # posture rules (mutable-install-reference) keep working off the ref.
        self_ref = ComponentRef(
            name=plugin_name,
            version=version,
            component_identity=component_identity,
            source_manifest=str(lockfile_path),
            source_locator=f"$.plugins.{plugin_key}[{index}]",
            extra={
                "component_type": "plugin",
                "runtime_hosts": ["claude-code"],
                "declared_by": {"kind": "skill_lock", "path": str(lockfile_path)},
                "component_path": [{"type": "plugin", "name": plugin_name}],
                "gitCommitSha": entry.get("gitCommitSha"),
                "installPath": entry.get("installPath"),
                "marketplace": marketplace,
                "scope": entry.get("scope"),
            },
        )
        plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
        _add_child(graph, target, plugin_node)

        install_path = entry.get("installPath")
        if isinstance(install_path, str) and install_path:
            # Reuse the repo-mode plugin descent for bundled skills + their deps,
            # but suppress the plugin's OWN root dep manifests: those come from
            # the tier-2 lockfile walk below (lockfile-preferred). Emitting both
            # would double-count a direct dep present in package.json AND
            # package-lock.json. Bundled skills and their own deps still descend.
            descend(graph, plugin_node, Path(install_path), normalize, emit_own_root_deps=False)
            # Plugin tier-2 lockfile deps: parity with parse_install — attach as
            # package children of the plugin node (NOT a skill).
            for ref in claude_install._walk_plugin_implementation_deps(Path(install_path)):
                node = Node(key=occurrence_key(ref, normalize), kind="package", ref=ref)
                _add_child(graph, plugin_node, node)


def _seed_remote_mcps(
    graph: Graph,
    target: Node,
    install_root: Path,
    project_root: Path | None,
    by_scope: dict,
    normalize: SourceNormalizer,
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
            node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
            _add_child(graph, target, node)

    # Standalone .mcp.json: user-scoped (<install_root>/.mcp.json) and
    # project-scoped (<project_root>/.mcp.json) — parity with
    # _walk_direct_components in claude_install.
    mcp_paths: list[Path] = [install_root / ".mcp.json"]
    if project_root is not None:
        mcp_paths.append(project_root / ".mcp.json")
    for mcp_path in mcp_paths:
        if not mcp_path.is_file():
            continue
        for ref in _safe_parse(mcp_json.parse, mcp_path):
            if _component_type(ref) != "mcp_server":
                continue
            node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
            _add_child(graph, target, node)


def _seed_direct_components(
    graph: Graph,
    target: Node,
    install_root: Path,
    project_root: Path | None,
    by_scope: dict,
    normalize: SourceNormalizer,
) -> None:
    """Seed the remaining `_walk_direct_components` surfaces as target children.

    These are direct components — declared outside any plugin — so their parent
    is the target (attribution None, by construction). Discovery reuses the
    `claude_install` sub-helpers so the occurrence content matches what
    `parse_install` produced.

    What is NOT seeded here (already owned by `_seed_endpoint`):
    - Project skills under `<project_root>/.claude/skills/` (`_add_project_skills`).
    - Remote MCPs from settings `mcpServers` and `.mcp.json` (`_seed_remote_mcps`).

    Seeding only the non-overlapping surfaces (rather than calling
    `_walk_direct_components` wholesale and relying on edge-dedup) keeps the two
    project-skill discovery paths from racing to own the node: their occurrence
    keys collide, so whichever ran first would silently win the ref content.
    """
    # Install-root direct skills: descend into each skill dir so its dep
    # manifests become package children of the skill node (parity with
    # `_add_skill_node` used for project skills and plugin-bundled skills).
    _add_direct_endpoint_skills(graph, target, install_root / "skills", normalize, project_root)

    # Personal commands/agents: per-file parse so agent frontmatter
    # mcpServers/hooks attach under the agent node, not the target (parity with
    # the `.md` branch of `_add_repo_standalone_components`).
    _add_endpoint_command_agents(
        graph, target, install_root / "commands", normalize, kind="command"
    )
    _add_endpoint_command_agents(graph, target, install_root / "agents", normalize, kind="agent")

    # Project commands/agents under `.claude/`.
    if project_root is not None:
        _add_endpoint_command_agents(
            graph, target, project_root / ".claude" / "commands", normalize, kind="command"
        )
        _add_endpoint_command_agents(
            graph, target, project_root / ".claude" / "agents", normalize, kind="agent"
        )

    # Settings-scoped hooks, per scope (no cross-scope merging — parity with
    # `_walk_direct_components`). Hooks are leaf children of the target.
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
        for ref in hooks_json.parse_settings_hooks(
            settings_path, scope_data.get("hooks"), scope=scope
        ):
            component_type = _component_type(ref)
            if not isinstance(component_type, str):
                continue
            node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
            _add_child(graph, target, node)


def _add_direct_endpoint_skills(
    graph: Graph,
    parent: Node,
    skills_dir: Path,
    normalize: SourceNormalizer,
    project_root: Path | None = None,
) -> None:
    """Endpoint install-root direct skills: one skill node per `<skills_dir>/<name>/`
    subdir with descent so the skill's dep manifests become package children
    (parity with `_add_skill_node` used for project skills and plugin skills).

    Provenance is stamped here (parity with `_parse_direct_skill`) because
    direct endpoint skills may have a `.skill-lock.json` alongside them that
    records their install source. Project skills and plugin-bundled skills do
    not go through this path.
    """
    if not skills_dir.is_dir():
        return
    for skill_subdir in sorted(skills_dir.iterdir()):
        if skill_subdir.name.startswith("."):
            continue
        skill_md = skill_subdir / "SKILL.md"
        if not skill_md.is_file():
            continue
        for ref in _safe_parse(claude_skill.parse, skill_md):
            if ref.name:
                provenance = skill_lock.provenance_for_skill(
                    skill_md, ref.name, project_root=project_root
                )
                if provenance is not None:
                    ref = replace(ref, extra={**ref.extra, "source_provenance": provenance})
            skill_node = Node(key=occurrence_key(ref, normalize), kind="skill", ref=ref)
            _add_child(graph, parent, skill_node)
            descend(graph, skill_node, skill_subdir, normalize)


def _add_endpoint_command_agents(
    graph: Graph, target: Node, dir_path: Path, normalize: SourceNormalizer, kind: Kind
) -> None:
    """Walk `dir_path/**/*.md` per-file so agent frontmatter mcpServers/hooks
    attach under their agent node rather than the target (parity with the `.md`
    branch of `_add_repo_standalone_components`)."""
    if not dir_path.is_dir():
        return
    for md_path in sorted(dir_path.rglob("*.md")):
        if not md_path.is_file():
            continue
        try:
            refs = claude_command_agent.parse_file(md_path, kind=kind, scope_owner=None)
        except Exception:
            refs = []
        if not refs:
            continue
        self_node = Node(key=occurrence_key(refs[0], normalize), kind=kind, ref=refs[0])
        _add_child(graph, target, self_node)
        for child_ref in refs[1:]:
            child_kind = _component_type(child_ref)
            if not isinstance(child_kind, str):
                continue
            child_node = Node(
                key=occurrence_key(child_ref, normalize), kind=child_kind, ref=child_ref
            )
            _add_child(graph, self_node, child_node)


def descend(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize: SourceNormalizer,
    *,
    emit_own_root_deps: bool = True,
    include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
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

    `emit_own_root_deps` gates ONLY the plugin branch's emission of the
    plugin's OWN root dep manifests (`_add_dep_manifest_packages` at
    `directory`). Endpoint seeding sets it `False` because the plugin's own
    deps come from the tier-2 lockfile walk (`_walk_plugin_implementation_deps`,
    lockfile-preferred) instead; emitting them here too would double-count a
    direct dep that appears in both `package.json` and `package-lock.json`.
    The flag does NOT affect bundled-skill discovery or nested skills' own
    deps — those descend through the `skill` branch, which always emits.

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
        plugin_roots = _find_plugin_roots(directory, include_gitignored=include_gitignored)
        # Only directories that actually produced a plugin node own their
        # subtree. A malformed/empty `plugin.json` yields no node, so its dir
        # must NOT be excluded from sibling discovery — otherwise one bad
        # manifest would silently hide an otherwise-valid `.mcp.json`, project
        # skill, or dep manifest in the same/under that directory.
        realized_roots: list[Path] = []
        for plugin_root in plugin_roots:
            plugin_node = _descend_into_plugin(
                graph,
                parent,
                plugin_root,
                plugin_root / ".claude-plugin" / "plugin.json",
                normalize,
                root_dir=root_dir,
                root_spec=root_spec,
            )
            if plugin_node is not None:
                realized_roots.append(plugin_root)
        _add_project_skills(
            graph,
            parent,
            directory,
            normalize=normalize,
            exclude_under=realized_roots,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
        )
        # A plugin root owns its own dep manifests (emitted under the plugin via
        # the plugin-branch descent); emitting them again under target would
        # double-parent the same occurrence and trip validate(). The target's
        # bare-dep walk is non-recursive (only `directory/`), so it only needs to
        # skip when `directory` itself is a realized plugin root.
        if not any(_same_path(directory, root) for root in realized_roots):
            _add_dep_manifest_packages(
                graph,
                parent,
                directory,
                normalize,
                include_gitignored=include_gitignored,
                root_dir=root_dir,
                root_spec=root_spec,
            )
        _add_repo_standalone_components(
            graph,
            parent,
            directory,
            normalize,
            exclude_under=realized_roots,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
        )
    elif parent.kind == "plugin":
        _add_bundled_skills(
            graph, parent, directory, normalize, root_dir=root_dir, root_spec=root_spec
        )
        _add_bundled_plugin_surfaces(
            graph, parent, directory, normalize, root_dir=root_dir, root_spec=root_spec
        )
        if emit_own_root_deps:
            _add_dep_manifest_packages(
                graph,
                parent,
                directory,
                normalize,
                include_gitignored=include_gitignored,
                root_dir=root_dir,
                root_spec=root_spec,
            )
    elif parent.kind == "skill":
        _add_dep_manifest_packages(
            graph,
            parent,
            directory,
            normalize,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
        )


def _same_path(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


def _find_plugin_roots(directory: Path, *, include_gitignored: bool = False) -> list[Path]:
    """Plugin roots are dirs containing `.claude-plugin/plugin.json`, at ANY
    depth (parity with parse_repo). Discovery uses the same gitignore-aware walk
    as project-skill discovery so we skip `node_modules/`, `.git/`, gitignored
    dirs. Returns each plugin root sorted for determinism.
    """
    spec = None if include_gitignored else load_gitignore_spec(directory)
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


def _attach_mcp_launch_deps(
    graph: Graph,
    scan_root: Path,
    normalize: SourceNormalizer,
    name_index: dict[tuple[str, str], Path],
    *,
    project_root: Path | None = None,
    include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    """ADR-0039: make `mcp_server` non-leaf. For each MCP node, resolve its
    launch target to a dependency-manifest dir and attach that dir's deps as
    `package` children. The resolved deps become `agent-dependency` via the
    existing `scope_of` (the `mcp_server` is in their lineage).

    Single-parent invariant: the resolved dir's deps may already be parented to
    `target` (the repo-root walk emitted them as software-dependency). For those,
    the MCP claims them — the stale `target` edge is dropped. If a dep is already
    owned by another agent component (e.g. its bundling plugin, or a different MCP
    that resolved the same dir first), that owner wins and this MCP's just-added
    edge is dropped instead. Deterministic node order makes "first claim" stable.
    """
    mcp_nodes = sorted(
        (n for n in graph.nodes.values() if n.kind == "mcp_server"), key=lambda n: n.key
    )
    for mcp in mcp_nodes:
        if mcp.ref is None:
            continue
        # Endpoint mode spans install_root and a separate project_root; a local
        # launch path declared in a project manifest resolves under project_root,
        # so use it as the scan_root when this MCP was declared there.
        effective_scan_root = scan_root
        if project_root is not None and mcp.ref.source_manifest:
            try:
                if Path(mcp.ref.source_manifest).resolve().is_relative_to(project_root.resolve()):
                    effective_scan_root = project_root
            except (ValueError, OSError):
                pass
        resolved = resolve_mcp_launch_dir(
            mcp.ref, scan_root=effective_scan_root, name_index=name_index
        )
        if resolved is None:
            continue
        before = {e.child for e in graph.edges if e.parent == mcp.key}
        _add_dep_manifest_packages(
            graph,
            mcp,
            resolved,
            normalize,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
        )
        new_children = {e.child for e in graph.edges if e.parent == mcp.key} - before
        for child_key in new_children:
            other_parents = {
                e.parent for e in graph.edges if e.child == child_key and e.parent != mcp.key
            }
            agent_owner = any(graph.nodes[pk].kind in Graph._AGENT_KINDS for pk in other_parents)
            if agent_owner:
                # Another agent component already owns this dep: don't steal it.
                graph.edges = [
                    e for e in graph.edges if not (e.child == child_key and e.parent == mcp.key)
                ]
            else:
                # MCP claims it from `target` (or it is freshly attached).
                graph.edges = [
                    e for e in graph.edges if e.child != child_key or e.parent == mcp.key
                ]


def build_manifest_name_index(
    scan_root: Path, *, include_gitignored: bool = False
) -> dict[tuple[str, str], Path]:
    """Map `(ecosystem, name)` → directory for each local package manifest.

    Used by ADR-0039 MCP launch resolution (strategy 1): an `npx`/`uvx <pkg>`
    launch resolves to a local dir when `<pkg>` matches a manifest `name` here
    (the repo *is* the package). npm `package.json` entries are keyed as
    `("npm", name)` and PyPI `pyproject.toml` entries as `("PyPI", name)`.
    Keying by ecosystem prevents `uvx foo` from resolving to a `package.json`
    named `foo`, or `npx foo` from resolving to a `pyproject.toml` named `foo`.
    The walk is gitignore-aware (skips `node_modules/`, `.git/`, etc.) like the
    others. Manifests under dependency/vendor directories (see
    `_NAME_INDEX_DEP_DIRS`) are always excluded regardless of
    `include_gitignored`, so that external `npx <pkg>` cannot resolve to an
    installed copy in `node_modules/`.
    """
    spec = None if include_gitignored else load_gitignore_spec(scan_root)
    index: dict[tuple[str, str], Path] = {}
    for path in iter_unignored_files(scan_root, spec):
        try:
            rel_dir_parts = path.relative_to(scan_root).parts[:-1]
        except ValueError:
            rel_dir_parts = path.parts[:-1]
        if any(p in _NAME_INDEX_DEP_DIRS for p in rel_dir_parts):
            continue
        # Skip installed-plugin cache subtrees so a direct/external `npx <pkg>`
        # launch can't name-match an unrelated cached plugin and attach its deps.
        # Installed plugins' own deps are attributed via the plugin descent path,
        # never this index (ADR-0039 endpoint review).
        # Two layouts observed in endpoint installs:
        #   `plugins/cache/<plugin>/...`  — Claude internal plugin cache dir
        #   `cache/<plugin>/<version>/...` — actual installPath from installed_plugins.json
        if any(
            rel_dir_parts[i] == "plugins" and rel_dir_parts[i + 1] == "cache"
            for i in range(len(rel_dir_parts) - 1)
        ) or (rel_dir_parts and rel_dir_parts[0] == "cache"):
            continue
        name: object = None
        ecosystem_key: str = ""
        if path.name == "package.json":
            try:
                name = json.loads(path.read_text()).get("name")
                ecosystem_key = "npm"
            except (json.JSONDecodeError, OSError, UnicodeDecodeError, AttributeError):
                continue
        elif path.name == "pyproject.toml":
            try:
                name = tomllib.loads(path.read_text()).get("project", {}).get("name")
                ecosystem_key = "PyPI"
            except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError, AttributeError):
                continue
        else:
            continue
        if isinstance(name, str) and name:
            if ecosystem_key == "PyPI":
                name = normalize_pypi_name(name)
            key = (ecosystem_key, name)
            if key not in index:
                index[key] = path.parent.resolve()
    return index


def _descend_into_plugin(
    graph: Graph,
    target: Node,
    plugin_root: Path,
    plugin_manifest: Path,
    normalize: SourceNormalizer,
    *,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> Node | None:
    """Create the plugin node (child of target) and descend into its subtree.

    Reuses `claude_plugin.parse` only to obtain the plugin self-identity ref;
    placement (the plugin → target edge, and which children hang off the
    plugin) is owned here.

    Returns the created plugin node, or `None` when the manifest is malformed
    or yields no self-ref. A `None` return means the directory is NOT an owned
    plugin subtree, so the caller must not exclude it from sibling discovery.
    """
    parsed = _safe_parse(claude_plugin.parse, plugin_manifest)
    self_ref = next((r for r in parsed if _component_type(r) == "plugin"), None)
    if self_ref is None:
        return None
    plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
    _add_child(graph, target, plugin_node)
    descend(graph, plugin_node, plugin_root, normalize, root_dir=root_dir, root_spec=root_spec)
    return plugin_node


def _add_project_skills(
    graph: Graph,
    parent: Node,
    directory: Path,
    exclude_under: list[Path] | None = None,
    *,
    normalize: SourceNormalizer,
    project_root: Path | None = None,
    stamp_provenance: bool = False,
    include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
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
    eval_root, spec = _ignore_context(directory, include_gitignored, root_dir, root_spec)
    # The walk yields paths relative to `directory`; ignore checks evaluate
    # relative to `eval_root` (the scan root in repo mode). When the walk root and
    # eval root differ, evaluate the absolute path against eval_root.
    walk_spec = spec if eval_root == directory else None
    exclude_resolved = [p.resolve() for p in exclude_under] if exclude_under else []
    for path in iter_unignored_files(directory, walk_spec):
        if path.name != "SKILL.md" or not _is_project_skill_md(path, directory):
            continue
        if _is_ignored_under(path, eval_root, spec):
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
            continue
        _add_skill_node(
            graph,
            parent,
            path.parent,
            normalize=normalize,
            project_root=project_root,
            stamp_provenance=stamp_provenance,
            root_dir=root_dir,
            root_spec=root_spec,
        )


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


def _add_bundled_skills(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize: SourceNormalizer,
    *,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
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
        _add_skills_from_dir(
            graph,
            parent,
            skills_dir,
            normalize=normalize,
            plugin_root=directory,
            root_dir=root_dir,
            root_spec=root_spec,
        )


def _plugin_custom_skills_field(plugin_root: Path) -> object:
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("skills")


def _add_skills_from_dir(
    graph: Graph,
    parent: Node,
    skills_dir: Path,
    *,
    normalize: SourceNormalizer,
    plugin_root: Path | None = None,
    project_root: Path | None = None,
    stamp_provenance: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    if not skills_dir.is_dir():
        return
    try:
        plugin_root_resolved = plugin_root.resolve() if plugin_root is not None else None
    except (OSError, RuntimeError):
        return
    eval_root, spec = _ignore_context(skills_dir, False, root_dir, root_spec)
    for skill_subdir in sorted(skills_dir.iterdir()):
        if skill_subdir.name.startswith("."):  # skip .DS_Store, .git, etc.
            continue
        if plugin_root_resolved is not None:
            try:
                subdir_resolved = skill_subdir.resolve()
            except (OSError, RuntimeError):
                continue
            if not subdir_resolved.is_relative_to(plugin_root_resolved):
                continue
        skill_md = skill_subdir / "SKILL.md"
        if not skill_md.is_file():
            continue
        if plugin_root_resolved is not None:
            try:
                skill_md_resolved = skill_md.resolve()
            except (OSError, RuntimeError):
                continue
            if not skill_md_resolved.is_relative_to(plugin_root_resolved):
                continue
        if _is_ignored_under(skill_md, eval_root, spec):
            continue
        _add_skill_node(
            graph,
            parent,
            skill_subdir,
            normalize=normalize,
            project_root=project_root,
            stamp_provenance=stamp_provenance,
            root_dir=root_dir,
            root_spec=root_spec,
        )


def _add_skill_node(
    graph: Graph,
    parent: Node,
    skill_subdir: Path,
    *,
    normalize: SourceNormalizer,
    project_root: Path | None = None,
    stamp_provenance: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    """Create a skill node (child of `parent`) and descend into its dep manifests.

    `stamp_provenance` is set ONLY by the endpoint project-skill walk
    (`_add_project_skills` invoked from `_seed_endpoint`), matching the old
    `_walk_project_skill_dirs` → `_parse_direct_skill` path that stamped
    `extra["source_provenance"]` from a `skills-lock.json` / symlink target.
    Repo-mode `.claude/skills` (old REGISTRY `claude_skill.parse`, no stamp) and
    plugin-bundled skills (old `walk_plugin_root`, no stamp) leave it False.
    """
    skill_md = skill_subdir / "SKILL.md"
    for ref in _safe_parse(claude_skill.parse, skill_md):
        if stamp_provenance and ref.name:
            provenance = skill_lock.provenance_for_skill(
                skill_md, ref.name, project_root=project_root
            )
            if provenance is not None:
                ref = replace(ref, extra={**ref.extra, "source_provenance": provenance})
        skill_node = Node(key=occurrence_key(ref, normalize), kind="skill", ref=ref)
        _add_child(graph, parent, skill_node)
        descend(graph, skill_node, skill_subdir, normalize, root_dir=root_dir, root_spec=root_spec)


def _component_type(ref: ComponentRef) -> object:
    return (ref.extra or {}).get("component_type")


def _safe_parse(parse, manifest: Path) -> list[ComponentRef]:
    """Run a leaf parser, swallowing per-manifest parse failures.

    These parsers run against arbitrary user repos; one malformed file (bad
    JSON, unreadable bytes) must not abort the whole graph build. This mirrors
    `parse_repo_grouped`'s per-path try/except — descent skips the bad file and
    continues.
    """
    try:
        return parse(manifest)
    except Exception:
        return []


def _add_dep_manifest_packages(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize: SourceNormalizer,
    *,
    include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    """Emit package children from `directory`'s dep manifests, lockfile-preferred
    per ecosystem (ADR-0008; parity with `_walk_plugin_implementation_deps`).

    For each ecosystem (npm: `package.json` ↔ `package-lock.json`/`bun.lock`;
    PyPI: `pyproject.toml` ↔ `uv.lock`), if a lockfile is present we emit ONLY
    the lockfile's deps (full transitive tree) and skip the manifest. The
    manifest is a fallback used only when no lockfile exists for that ecosystem.
    Without this, a dir with BOTH `package.json` and `package-lock.json` emits
    two nodes for the same direct dep (their occurrence keys differ by
    `source_manifest`, so they never dedup), double-reporting one package.

    Unlike `_walk_plugin_implementation_deps`, the refs are emitted as the leaf
    parsers produce them (`transitive=True` on lockfile refs) — only the
    file-selection logic is shared.
    """
    eval_root, spec = _ignore_context(directory, include_gitignored, root_dir, root_spec)

    def _present(filename: str) -> Path | None:
        manifest = directory / filename
        if not manifest.is_file():
            return None
        if _is_ignored_under(manifest, eval_root, spec):
            return None
        return manifest

    covered: set[str] = set()
    for ecosystem, filename in claude_install._LOCKFILE_DISPATCH_FILES:
        if ecosystem in covered:
            continue
        manifest = _present(filename)
        if manifest is None:
            continue
        emitted = False
        for ref in _safe_parse(_DEP_MANIFEST_PARSERS[filename], manifest):
            node = Node(key=occurrence_key(ref, normalize), kind="package", ref=ref)
            _add_child(graph, parent, node)
            emitted = True
        if emitted:
            covered.add(ecosystem)
    for ecosystem, filename in claude_install._MANIFEST_FALLBACK_FILES:
        if ecosystem in covered:
            continue
        manifest = _present(filename)
        if manifest is None:
            continue
        for ref in _safe_parse(_DEP_MANIFEST_PARSERS[filename], manifest):
            node = Node(key=occurrence_key(ref, normalize), kind="package", ref=ref)
            _add_child(graph, parent, node)


def _ignore_context(
    directory: Path,
    include_gitignored: bool,
    root_dir: Path | None,
    root_spec: GitIgnoreSpec | None,
) -> tuple[Path, GitIgnoreSpec | None]:
    """Resolve which (root, spec) a gitignore check should evaluate against.

    Repo mode threads the SCAN ROOT and its spec down through every nested
    descent so a root `.gitignore` rule is honored even inside plugin/skill
    subtrees (parity with parse_repo_grouped, which loads the root spec once and
    evaluates root-relative). When no root is threaded (endpoint mode for
    installed-plugin / install-root surfaces), apply NO gitignore filtering:
    installed artifacts are not repo source, and the old `parse_install` /
    `walk_plugin_root` paths never filtered them by a `.gitignore`. The one
    endpoint surface that DID filter (project skills, via the project root's
    `.gitignore`) threads `root_dir=project_root` explicitly, so it takes the
    `root_dir is not None` branch.
    """
    if root_dir is not None:
        return root_dir, root_spec
    return directory, None


def _is_ignored_under(path: Path, eval_root: Path, spec: GitIgnoreSpec | None) -> bool:
    """Evaluate `is_ignored(path relative-to eval_root)`, guarding paths that
    are not under `eval_root` (skip the ignore check for those, matching the
    per-directory fallback's reach)."""
    try:
        rel = path.relative_to(eval_root)
    except ValueError:
        return False
    return spec is not None and is_ignored(rel, spec)  # type: ignore[arg-type]


# Standalone MCP manifest filenames discovered at any depth in repo mode
# (parity with the REGISTRY `mcp.json` / `.mcp.json` / `claude_desktop_config.json`
# patterns, which match by bare name anywhere in the tree).
_STANDALONE_MCP_FILENAMES = ("mcp.json", ".mcp.json", "claude_desktop_config.json")

# `.claude/<subdir>/**/*.md` agent-component surfaces discovered at any depth in
# repo mode, mirroring the REGISTRY command/agent patterns.
_COMMAND_AGENT_SURFACES: tuple[tuple[str, Kind], ...] = (
    ("commands", "command"),
    ("agents", "agent"),
)


def _add_repo_standalone_components(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize: SourceNormalizer,
    *,
    exclude_under: list[Path] | None = None,
    include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    """Repo target-level standalone surfaces: MCP manifests and `.claude`
    commands/agents discovered at any depth (parity with the parser REGISTRY),
    each a child of the target.

    Files inside a plugin subtree are skipped (`exclude_under` = the plugin
    roots already descended from the target) so a plugin's bundled MCP/command
    surfaces stay under the plugin node (single-parent).
    """
    eval_root, spec = _ignore_context(directory, include_gitignored, root_dir, root_spec)
    walk_spec = spec if eval_root == directory else None
    exclude_resolved = [p.resolve() for p in exclude_under] if exclude_under else []
    for path in iter_unignored_files(directory, walk_spec):
        if _is_ignored_under(path, eval_root, spec):
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
            continue
        if path.name in _STANDALONE_MCP_FILENAMES:
            for ref in _safe_parse(mcp_json.parse, path):
                if _component_type(ref) != "mcp_server":
                    continue
                node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
                _add_child(graph, parent, node)
            continue
        if path.name == "settings.json" and _is_claude_settings_json(path, directory):
            for ref in _safe_parse(claude_settings.parse, path):
                if _component_type(ref) != "plugin":
                    continue
                node = Node(key=occurrence_key(ref, normalize), kind="plugin", ref=ref)
                _add_child(graph, parent, node)
            continue
        if path.suffix == ".md":
            kind = _command_agent_kind(path, directory)
            if kind is None:
                continue
            try:
                refs = claude_command_agent.parse_file(path, kind=kind)
            except Exception:
                refs = []
            if not refs:
                continue
            self_node = Node(key=occurrence_key(refs[0], normalize), kind=kind, ref=refs[0])
            _add_child(graph, parent, self_node)
            # Agents may declare frontmatter mcpServers/hooks; parse_file returns
            # them as subsequent refs. Attach them under the agent node (not the
            # target) with their own kinds so scope_of / lineage see the agent ancestor.
            for child_ref in refs[1:]:
                child_kind = _component_type(child_ref)
                if not isinstance(child_kind, str):
                    continue
                child_node = Node(
                    key=occurrence_key(child_ref, normalize), kind=child_kind, ref=child_ref
                )
                _add_child(graph, self_node, child_node)


def _is_claude_settings_json(path: Path, root: Path) -> bool:
    """True iff `path` is `.claude/settings.json` at any depth relative to root."""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return False
    return rel == ".claude/settings.json" or rel.endswith("/.claude/settings.json")


def _command_agent_kind(path: Path, root: Path) -> Kind | None:
    """Return `"command"`/`"agent"` if `path` is a `.md` under a
    `.claude/commands/` or `.claude/agents/` dir at any depth, else None."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    for subdir, kind in _COMMAND_AGENT_SURFACES:
        for i in range(len(parts) - 2):
            if parts[i] == ".claude" and parts[i + 1] == subdir:
                return kind
    return None


def _add_bundled_plugin_surfaces(
    graph: Graph,
    plugin_node: Node,
    plugin_root: Path,
    normalize: SourceNormalizer,
    *,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    """A plugin's bundled non-skill surfaces (MCPs, hooks, commands, agents) →
    children of the plugin node. Reuses the shared `claude_plugin_root` helpers
    for content; placement is owned here (parent-by-construction).

    Bundled skills are NOT added here — the `_add_bundled_skills` descent already
    creates them and their dep chains; re-emitting via the surface walker would
    double-create. Parentage of every bundled surface is set by the graph edge
    from the plugin node below, not stored on the refs.
    """
    plugin_ref = plugin_node.ref
    if plugin_ref is None:
        return
    plugin_name = plugin_ref.name or ""
    plugin_data = _plugin_manifest_data(plugin_root)
    plugin_manifest_path = plugin_root / ".claude-plugin" / "plugin.json"

    refs: list[ComponentRef] = []
    manifest_refs = _parse_manifest_refs(
        plugin_data,
        plugin_json_path=plugin_manifest_path,
        plugin_root=plugin_root,
    )
    refs.extend(manifest_refs)
    refs.extend(_parse_default_mcp(plugin_root, manifest_refs))
    refs.extend(_parse_bundled_hooks(plugin_root, plugin_data, plugin_name))
    refs.extend(_parse_bundled_command_agents(plugin_root, plugin_data, plugin_name))
    refs = [r for r in refs if _component_type(r) != "skill"]
    # Stamp plugin-container context (declared_by.kind=plugin + a
    # plugin-prefixed component_path) onto each bundled ref. This is placement
    # metadata the descent owns — parity with the pre-graph `_with_plugin_context`
    # that the endpoint walker applied — not a content read.
    refs = claude_install._with_plugin_context(refs, plugin_name, plugin_manifest_path)
    # Honor the scan-root .gitignore in repo mode (parity with parse_repo_grouped,
    # which filters secondary refs): a bundled surface declared in a file the root
    # ignores (e.g. a plugin repo with `.mcp.json` gitignored) must not be emitted.
    # Endpoint mode passes root_dir=None → _ignore_context returns spec=None so the
    # installed plugin's OWN .gitignore is never applied (parity with the old
    # walk_plugin_root, which did not filter installed-plugin surfaces).
    eval_root, spec = _ignore_context(plugin_root, False, root_dir, root_spec)
    for ref in refs:
        component_type = _component_type(ref)
        if not isinstance(component_type, str):
            continue
        if ref.source_manifest and _is_ignored_under(Path(ref.source_manifest), eval_root, spec):
            continue
        node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
        _add_child(graph, plugin_node, node)


def _plugin_manifest_data(plugin_root: Path) -> dict:
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
