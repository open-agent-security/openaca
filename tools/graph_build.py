"""Construct the composition graph by recursive descent over a target.

`build_graph(target, mode)` walks the target and produces a `Graph` whose
edges encode parentage: the synthetic target root, then a child node per
discovered component or package, recursing into each.

`graph_build` owns *placement* (which parent a node hangs from); the leaf
parsers in `tools.parsers` own *content* (what a manifest declares). Scope is
never stamped on the ref here — it is derived from the graph (`Graph.scope_of`).

Node identity is the *occurrence bom-ref* (ADR-0042), never the bare purl: two
manifests declaring the same purl yield two distinct package nodes. The target
root's key is a fixed logical value (`openaca:target`) so repo BOMs are
reproducible across machines — the resolved scan path is evidence, not identity.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pathspec import GitIgnoreSpec

from tools.component_ref import ComponentRef
from tools.endpoint_surface import CLAUDE_CODE_ENDPOINT, CODEX_ENDPOINT, EndpointSurface
from tools.graph import Edge, Graph, Node, WarningLog, record_gap
from tools.identity import canonical_component_identity, finalize_component_identity
from tools.mcp_launch_resolve import normalize_pypi_name, resolve_mcp_launch_dir
from tools.parsers import (
    bun_lock,
    claude_command_agent,
    claude_install,
    claude_plugin,
    claude_settings,
    claude_skill,
    codex_agent,
    codex_config,
    codex_rules,
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
from tools.parsers.settings_layers import SCOPE_PRECEDENCE, default_managed_dir
from tools.parsers.settings_layers import load as load_settings
from tools.repo_surface import CLAUDE_CODE_SURFACE, CODEX_SURFACE, PluginFormat, RepoSurface

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
    parent_node = graph.nodes.get(parent_node.key, parent_node)
    if child_node.ref is not None:
        parent_namespace = None
        if parent_node.ref is not None:
            candidate = (parent_node.ref.extra or {}).get("_identity_namespace")
            if isinstance(candidate, str) and candidate:
                parent_namespace = candidate
        child_node = replace(
            child_node,
            ref=finalize_component_identity(
                child_node.ref,
                parent_identity=parent_namespace,
            ),
        )
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
    mode: str,
    target: Path,
    install_root: Path,
    project_root: Path | None,
    root_label: str = "endpoint",
    extra_roots: tuple[tuple[str, Path], ...] = (),
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

    `extra_roots` are additional labeled roots checked (longest path first, so a
    nested root wins over an ancestor) after `project_root` and before
    `install_root`. Claude Code passes none; a second kind with more than one
    config root threads them here instead of forking the normalizer.
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
    extra_roots_sorted = sorted(
        ((label, root, root.resolve()) for label, root in extra_roots),
        key=lambda entry: len(str(entry[1])),
        reverse=True,
    )

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
        for label, root_logical, root_resolved in extra_roots_sorted:
            rel = _rel(abs_path, root_logical, root_resolved)
            if rel is not None:
                return f"{label}/{rel}"
        rel = _rel(abs_path, install_root, install_r)
        if rel is not None:
            return f"{root_label}/{rel}"
        return abs_path

    return normalize


def occurrence_key(ref: ComponentRef, normalize: SourceNormalizer = _identity_normalizer) -> str:
    """The node key for a ref: its occurrence key, never the bare purl.

    The key is the occurrence — where the ref was declared
    (source_manifest + source_locator) plus what it is — never the bare
    component identity or display/source coordinate. So two same-named skills
    at different paths, or two
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
    """Legacy place-rooted graph. Retained for `tools/remote/collector.py`, whose
    upload contract keeps `endpoint/` labels and the `openaca:target` root ref
    until the collector is migrated to agent discovery."""
    return build_rooted_graph(
        target,
        mode,
        root_key=_TARGET_KEY,
        root_label="endpoint",
        project_root=project_root,
        include_gitignored=include_gitignored,
        warnings=warnings,
    )


def build_rooted_graph(
    target: Path,
    mode: str,
    *,
    root_key: str,
    root_label: str = "endpoint",
    project_root: Path | None = None,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
    surface: RepoSurface = CLAUDE_CODE_SURFACE,
    endpoint_surface: EndpointSurface = CLAUDE_CODE_ENDPOINT,
) -> Graph:
    if mode not in ("repo", "endpoint"):
        raise ValueError(f"unknown mode: {mode!r}")

    root = Node(key=root_key, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    # The node-key path normalizer (Stage 4): strips the machine-specific scan
    # root so node keys — which become CycloneDX bom-refs — are reproducible.
    # The gitignore root (`root_dir`/`root_spec`) and the normalize root derive
    # from the same scan root; they're separate concerns threaded in parallel.
    normalize = _make_normalizer(mode, Path(target), Path(target), project_root, root_label)
    # ADR-0039 launch resolution context, set per-branch below.
    attach_root_dir: Path | None = None
    attach_root_spec: GitIgnoreSpec | None = None
    attach_include_gitignored = include_gitignored
    if mode == "endpoint":
        _seed_endpoint(
            graph,
            root,
            Path(target),
            project_root,
            normalize,
            surface=endpoint_surface,
            warnings=graph.warnings,
        )
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
            surface=surface,
        )
        attach_root_dir = root_dir
        attach_root_spec = root_spec
    return finalize_graph(
        graph,
        Path(target),
        normalize,
        project_root=project_root,
        include_gitignored=include_gitignored,
        attach_include_gitignored=attach_include_gitignored,
        root_dir=attach_root_dir,
        root_spec=attach_root_spec,
        warnings=warnings,
    )


def finalize_graph(
    graph: Graph,
    target: Path,
    normalize: SourceNormalizer,
    *,
    project_root: Path | None = None,
    include_gitignored: bool = False,
    attach_include_gitignored: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
    warnings: list[str] | None = None,
) -> Graph:
    """The `build_rooted_graph` tail, shared by every kind's composer: build the
    manifest name index, merge in `project_root`'s (endpoint mode only), attach
    ADR-0039 MCP launch-dependency packages, and validate.

    A second kind's `compose` calls this same function after seeding/descending
    its own graph so launch resolution never forks per kind.
    """
    name_index = build_manifest_name_index(target, include_gitignored=attach_include_gitignored)
    if project_root is not None:
        # Endpoint mode: project_root is separate from install_root (target),
        # so its manifests are absent from the target walk. Merge them in so
        # that a project-scoped MCP declaring `npx <pkg>` can resolve by name
        # against the project's own package.json / pyproject.toml.
        # project_root entries take precedence over install_root entries.
        name_index = {
            **name_index,
            # project_root is a user project dir — respect its .gitignore (matching
            # project-skill filtering at _seed_endpoint line ~343). Only install_root
            # artifacts need the unfiltered walk (attach_include_gitignored=True).
            **build_manifest_name_index(project_root, include_gitignored=include_gitignored),
        }
    _attach_mcp_launch_deps(
        graph,
        target,
        normalize,
        name_index,
        project_root=project_root,
        include_gitignored=attach_include_gitignored,
        project_root_include_gitignored=include_gitignored,
        root_dir=root_dir,
        root_spec=root_spec,
    )
    graph.validate()
    if warnings is not None:
        # `absorb` rather than `extend`, so the caller's list keeps knowing
        # which of these are component gaps — that distinction is what
        # `composition_coverage` reads.
        if isinstance(warnings, WarningLog):
            warnings.absorb(graph.warnings)
        else:
            warnings.extend(graph.warnings)
    return graph


def _seed_endpoint(
    graph: Graph,
    target: Node,
    install_root: Path,
    project_root: Path | None,
    normalize: SourceNormalizer,
    *,
    surface: EndpointSurface = CLAUDE_CODE_ENDPOINT,
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
    layers = load_settings(install_root, project_root=project_root, warnings=warnings)
    effective = layers.merged("endpoint")
    by_scope = layers.by_scope()

    plugins_map, lockfile_path, plugin_warnings = claude_install._load_plugins_map(install_root)
    if warnings is not None:
        warnings.extend(plugin_warnings)
    enabled = effective.get("enabledPlugins", {})
    if not isinstance(enabled, dict):
        if "enabledPlugins" in effective and warnings is not None:
            record_gap(warnings, "enabledPlugins must be an object")
    else:
        for plugin_key, is_enabled in enabled.items():
            if not isinstance(plugin_key, str):
                if warnings is not None:
                    record_gap(warnings, "enabledPlugins keys must be plugin@marketplace strings")
                continue
            plugin_name, marketplace = claude_install._split_plugin_key(plugin_key)
            if not plugin_name or not marketplace:
                if warnings is not None:
                    warnings.append("enabledPlugins keys must be plugin@marketplace strings")
            if not isinstance(is_enabled, bool) and warnings is not None:
                record_gap(warnings, f"enabledPlugins.{plugin_key} must be a boolean")
        if any(value is True for value in enabled.values()):
            if plugins_map is None or lockfile_path is None:
                if warnings is not None:
                    record_gap(
                        warnings, "enabled plugins but installed_plugins.json is unavailable"
                    )
            else:
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

    _seed_shared_endpoint_surfaces(
        graph,
        target,
        install_root,
        project_root,
        normalize,
        surface=surface,
        by_scope=by_scope,
    )

    _seed_remote_mcps(graph, target, install_root, project_root, by_scope, normalize)


def _seed_shared_endpoint_surfaces(
    graph: Graph,
    target: Node,
    install_root: Path,
    project_root: Path | None,
    normalize: SourceNormalizer,
    *,
    surface: EndpointSurface = CLAUDE_CODE_ENDPOINT,
    repo_surface: RepoSurface = CLAUDE_CODE_SURFACE,
    by_scope: dict | None = None,
) -> None:
    """The two endpoint surfaces whose procedure is shared across kinds
    (ADR-0057): project skills, and direct components.

    Callable on its own, which is the point. A kind that forks plugin
    acquisition and remote MCP calls THIS rather than `_seed_endpoint` — the
    latter also runs Claude Code's own acquisition unconditionally, against
    whatever root it was handed.

    `by_scope` is only read when `surface.seeds_hooks` is true, so a kind that
    seeds no settings-scoped hooks passes `None` rather than assembling a
    settings structure it does not have.

    `repo_surface` is the `RepoSurface` counterpart of `surface`: passed
    through to `_add_project_skills`, whose `.claude`-vs-`.codex` (and
    one-level-vs-recursive) skill-directory match is a `RepoSurface` field, not
    an `EndpointSurface` one. Left at the Claude Code default, a Codex endpoint
    scan would recognise `<project>/.claude/skills/**/SKILL.md` as its own
    project skills — a Claude-only surface, not Codex's.
    """
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
            surface=repo_surface,
        )
        # iterdir() follows symlinks; os.walk (used by iter_unignored_files) does
        # not. Call one of the two symlink-patch helpers below explicitly so
        # symlinked skill dirs under <project>/<config_dir>/skills/ are
        # discovered — parity with the old _walk_project_skill_dirs path that
        # called _walk_skill_dir (iterdir-based) before iter_unignored_files.
        # _add_child dedup collapses non-symlink dupes. stamp_provenance
        # matches _parse_direct_skill, which both old project-skill walks
        # shared.
        #
        # `repo_surface.skill_config_dirs` (not `surface`, the EndpointSurface
        # param) is the recursion signal: it is what `_is_project_skill_md`
        # itself checks to decide whether nesting below the skills dir is a
        # project skill at all. A flat, direct-child-only symlink patch is
        # correct for Claude Code's one-level project skills, but silently
        # under-recurses for a kind like Codex whose project skills nest
        # (`.codex/skills/team/tool -> ...`), so that case gets the same
        # cycle-safe recursive walker `_add_direct_endpoint_skills` already
        # uses for install-root skills.
        skills_dir = project_root / surface.project_config_dir / surface.project_skills_subdir
        if repo_surface.skill_config_dirs:
            _add_project_skills_from_dir_following_symlinks(
                graph,
                target,
                skills_dir,
                normalize=normalize,
                project_root=project_root,
                stamp_provenance=True,
                root_dir=project_root,
                root_spec=project_skill_spec,
            )
        else:
            _add_skills_from_dir(
                graph,
                target,
                skills_dir,
                normalize=normalize,
                project_root=project_root,
                stamp_provenance=True,
            )

    _seed_direct_components(
        graph,
        target,
        install_root,
        project_root,
        by_scope,
        normalize,
        surface=surface,
    )


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
                record_gap(
                    warnings, f"plugin {plugin_key} enabled but missing from installed_plugins.json"
                )
            continue
        entries = [(i, e) for i, e in enumerate(raw_entries) if isinstance(e, dict)]
        if len(entries) != len(raw_entries) and warnings is not None:
            record_gap(warnings, f"plugin {plugin_key}: contains an invalid install entry")
        if not entries:
            if warnings is not None:
                record_gap(warnings, f"plugin {plugin_key}: no valid install entries; skipping")
            continue
        scope = claude_install._enabling_scope(plugin_key, layers, "endpoint")
        entry, index, warning = claude_install._select_install_entry(entries, scope)
        if warning is not None and warnings is not None:
            warnings.append(f"{plugin_key}: {warning}")

        plugin_name, marketplace = claude_install._split_plugin_key(plugin_key)
        version = entry.get("version")
        if version is not None and not isinstance(version, str):
            if warnings is not None:
                record_gap(
                    warnings,
                    f"{plugin_key}: non-string version {version!r} in "
                    "installed_plugins.json; skipping",
                )
            continue
        component_identity = claude_install._plugin_identity(plugin_name, marketplace)
        try:
            marketplace_source = claude_install._marketplace_source(layers, "endpoint", marketplace)
        except ValueError as exc:
            if warnings is not None:
                warnings.append(f"plugin {plugin_key}: invalid marketplace source ({exc})")
            marketplace_source = None
        extra = {
            "component_type": "plugin",
            "declared_by": {"kind": "skill_lock", "path": str(lockfile_path)},
            "component_path": [{"type": "plugin", "name": plugin_name}],
            "gitCommitSha": entry.get("gitCommitSha"),
            "installPath": entry.get("installPath"),
            "marketplace": marketplace,
            "scope": entry.get("scope"),
        }
        if marketplace_source is not None:
            extra["marketplace_source"] = marketplace_source

        # Carry the same plugin metadata `parse_install` emitted so endpoint
        # renderers (gitCommitSha display, per-plugin tier-2 coverage) and
        # posture rules (mutable-install-reference) keep working off the ref.
        self_ref = ComponentRef(
            name=plugin_name,
            version=version,
            component_identity=component_identity,
            source_manifest=str(lockfile_path),
            source_locator=f"$.plugins.{plugin_key}[{index}]",
            extra=extra,
        )
        plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
        _add_child(graph, target, plugin_node)

        install_path = entry.get("installPath")
        if not isinstance(install_path, str) or not install_path:
            if warnings is not None:
                record_gap(warnings, f"plugin {plugin_key}: missing installPath; skipping contents")
            continue
        install_dir = Path(install_path)
        if not install_dir.is_dir():
            if warnings is not None:
                record_gap(
                    warnings, f"plugin {plugin_key}: installPath {install_path!r} is unavailable"
                )
            continue
        # Reuse the repo-mode plugin descent for bundled skills + their deps,
        # but suppress the plugin's OWN root dep manifests: those come from
        # the tier-2 lockfile walk below (lockfile-preferred). Emitting both
        # would double-count a direct dep present in package.json AND
        # package-lock.json. Bundled skills and their own deps still descend.
        descend(
            graph,
            plugin_node,
            install_dir,
            normalize,
            emit_own_root_deps=False,
        )
        # Plugin tier-2 lockfile deps: parity with parse_install — attach as
        # package children of the plugin node (NOT a skill).
        for ref in claude_install._walk_plugin_implementation_deps(
            install_dir, warnings=warnings, strict=True
        ):
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
        # An administrator's system-wide policy. It can declare `mcpServers`
        # and `hooks`, so skipping it reported an MDM-managed endpoint's
        # composition as complete while missing components it genuinely has.
        # Provenance points at the base file even when a `managed-settings.d`
        # drop-in supplied the value — the scope is what identity keys on, and
        # the merged layer has one representative path.
        "managed": default_managed_dir() / "managed-settings.json",
        "user": install_root / "settings.json",
        "project": (project_root / ".claude" / "settings.json")
        if project_root is not None
        else None,
        "local": (project_root / ".claude" / "settings.local.json")
        if project_root is not None
        else None,
    }
    for scope in SCOPE_PRECEDENCE:
        settings_path = scope_to_settings_path.get(scope)
        if settings_path is None:
            continue
        scope_data = by_scope.get(scope) or {}
        if "mcpServers" not in scope_data:
            continue
        mcp_servers = scope_data["mcpServers"]
        if not isinstance(mcp_servers, dict):
            graph.record_gap(f"could not parse {settings_path}: mcpServers must be an object")
            continue
        try:
            refs = mcp_json.parse_mcp_servers(
                mcp_servers,
                source_manifest=str(settings_path),
                locator_prefix="$.mcpServers (inlined)",
                strict=True,
            )
        except ValueError as exc:
            graph.record_gap(f"could not parse {settings_path}: {exc}")
            continue
        for ref in refs:
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
        for ref in _safe_parse(graph, lambda path: mcp_json.parse(path, strict=True), mcp_path):
            if _component_type(ref) != "mcp_server":
                continue
            node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
            _add_child(graph, target, node)


def _seed_direct_components(
    graph: Graph,
    target: Node,
    install_root: Path,
    project_root: Path | None,
    by_scope: dict | None,
    normalize: SourceNormalizer,
    *,
    surface: EndpointSurface = CLAUDE_CODE_ENDPOINT,
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
    if surface.direct_skills_dir is not None:
        _add_direct_endpoint_skills(
            graph, target, install_root / surface.direct_skills_dir, normalize, project_root
        )

    # Personal commands/agents: per-file parse so agent frontmatter
    # mcpServers/hooks attach under the agent node, not the target (parity with
    # the `.md` branch of `_add_repo_standalone_components`). A kind whose
    # agents are not markdown supplies no dirs here and seeds them itself —
    # the parser choice is not a name this descriptor could carry.
    for dirname, kind in surface.direct_command_agent_dirs:
        _add_endpoint_command_agents(graph, target, install_root / dirname, normalize, kind=kind)

    # Project commands/agents under the kind's project config dir.
    if project_root is not None and surface.seeds_project_command_agents:
        for dirname, kind in surface.direct_command_agent_dirs:
            _add_endpoint_command_agents(
                graph,
                target,
                project_root / surface.project_config_dir / dirname,
                normalize,
                kind=kind,
            )

    # Settings-scoped hooks, per scope (no cross-scope merging — parity with
    # `_walk_direct_components`). Hooks are leaf children of the target.
    #
    # `seeds_hooks` is an absence, not a switch: a kind that declares hooks in
    # a repo rather than at its endpoint has no settings-scoped hook surface at
    # all, so there is nothing here to parameterise differently. `by_scope` is
    # only read past this gate, which is what lets such a kind pass `None`.
    if not surface.seeds_hooks:
        return
    by_scope = by_scope or {}
    scope_to_settings_path = {
        # An administrator's system-wide policy. It can declare `mcpServers`
        # and `hooks`, so skipping it reported an MDM-managed endpoint's
        # composition as complete while missing components it genuinely has.
        # Provenance points at the base file even when a `managed-settings.d`
        # drop-in supplied the value — the scope is what identity keys on, and
        # the merged layer has one representative path.
        "managed": default_managed_dir() / "managed-settings.json",
        "user": install_root / "settings.json",
        "project": (project_root / surface.project_config_dir / "settings.json")
        if project_root is not None
        else None,
        "local": (project_root / surface.project_config_dir / "settings.local.json")
        if project_root is not None
        else None,
    }
    for scope in SCOPE_PRECEDENCE:
        settings_path = scope_to_settings_path.get(scope)
        if settings_path is None:
            continue
        scope_data = by_scope.get(scope) or {}
        if "hooks" not in scope_data:
            continue
        try:
            hook_refs = hooks_json.parse_settings_hooks(
                settings_path, scope_data["hooks"], scope=scope, strict=True
            )
        except ValueError as exc:
            graph.record_gap(f"could not parse {settings_path}: {exc}")
            continue
        for ref in hook_refs:
            component_type = _component_type(ref)
            if not isinstance(component_type, str):
                continue
            node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
            _add_child(graph, target, node)


def _iter_skill_subdirs_following_symlinks(graph: Graph, skills_dir: Path) -> list[Path]:
    """Every subdirectory beneath `skills_dir`, following symlinks at any depth.

    `Path.rglob`/`Path.walk()` default to `follow_symlinks=False`, so a
    symlinked directory entry classifies as a non-directory and is excluded
    from traversal — not just "not recursed past," excluded outright, at
    every level of the walk (verified against CPython's own `pathlib`/`os.walk`
    source). That drops a symlinked skill whether it sits directly under
    `skills_dir` (e.g. `skills/aws-api -> /store/aws-api`) or nested under a
    real directory (e.g. `skills/team/aws-api -> /store/aws-api`) — `iterdir()`
    resolves a symlink entry like any other, so walking with it instead
    recovers both cases in one pass, with no separate direct-child patch.

    Cycle-safe: each directory's resolved (real) path is recorded before its
    children are visited, so a symlink loop — a directory symlinking to an
    ancestor, directly or through another symlink — is visited once rather
    than recursed into forever.

    A directory the walk has already identified but cannot enumerate (e.g.
    permission denied) is a readable surface the scan could not finish
    reading, not an empty one — `record_gap` so `composition_coverage`
    reflects the unread skills beneath it, rather than silently reporting the
    subtree as absent.
    """
    found: list[Path] = []
    seen: set[Path] = set()
    stack: list[Path] = [skills_dir]
    while stack:
        directory = stack.pop()
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            entries = sorted(directory.iterdir())
        except OSError as exc:
            graph.record_gap(f"could not list {directory}: {exc}")
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if not is_dir:
                continue
            found.append(entry)
            stack.append(entry)
    return found


def _add_direct_endpoint_skills(
    graph: Graph,
    parent: Node,
    skills_dir: Path,
    normalize: SourceNormalizer,
    project_root: Path | None = None,
) -> None:
    """Endpoint install-root direct skills: one skill node per `SKILL.md` found
    at any depth beneath `skills_dir`, with descent so the skill's dep
    manifests become package children (parity with `_add_skill_node` used for
    project skills and plugin skills).

    Recursive, matching every other `SKILL.md` walk in this module (project
    skills, repo-mode skills): `docs/specs/codex-agent-kind.md`'s surface table
    documents `<root>/skills/` as recursive for both declared and installed
    sources, and this is the shared branch `EndpointSurface`/ADR-0057 route
    Codex's install-root skills through, so it must actually recurse. A
    dot-prefixed directory at any depth is skipped by
    `_iter_skill_subdirs_following_symlinks` — this is what excludes Codex's
    vendor `skills/.system/` root here, ahead of (and independent of)
    `_prune_codex_system_skills`'s marker-based belt-and-suspenders pass.

    Provenance is stamped here (parity with `_parse_direct_skill`) because
    direct endpoint skills may have a `.skill-lock.json` alongside them that
    records their install source. Project skills and plugin-bundled skills do
    not go through this path.
    """
    if not skills_dir.is_dir():
        return
    for skill_subdir in sorted(
        _iter_skill_subdirs_following_symlinks(graph, skills_dir), key=lambda p: str(p)
    ):
        skill_md = skill_subdir / "SKILL.md"
        # `skill_subdir` was discoverable (its parent could list it), but its
        # own permission bits gate stat'ing anything inside it — a directory
        # listed but unreadable would otherwise raise `PermissionError` out of
        # `is_file()` uncaught (Python's `Path.is_file()` only swallows
        # ENOENT/ENOTDIR/EBADF/ELOOP, not EACCES), aborting the whole scan
        # instead of degrading this one skill to a gap.
        try:
            is_skill = skill_md.is_file()
        except OSError as exc:
            graph.record_gap(f"could not read {skill_md}: {exc}")
            continue
        if not is_skill:
            continue
        for ref in _safe_parse(graph, lambda path: claude_skill.parse(path, strict=True), skill_md):
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
    graph: Graph,
    target: Node,
    dir_path: Path,
    normalize: SourceNormalizer,
    kind: Kind,
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
            refs = claude_command_agent.parse_file(
                md_path, kind=kind, scope_owner=None, strict=kind == "agent"
            )
        except Exception as exc:
            graph.record_gap(f"could not parse agent definition {md_path}: {exc}")
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
    surface: RepoSurface = CLAUDE_CODE_SURFACE,
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
        plugin_roots = _find_plugin_roots(directory, surface, include_gitignored=include_gitignored)
        # Only directories that actually produced a plugin node own their
        # subtree. A malformed/empty `plugin.json` yields no node, so its dir
        # must NOT be excluded from sibling discovery — otherwise one bad
        # manifest would silently hide an otherwise-valid `.mcp.json`, project
        # skill, or dep manifest in the same/under that directory.
        realized_roots: list[Path] = []
        for plugin_root, plugin_format in plugin_roots:
            plugin_node = _descend_into_plugin(
                graph,
                parent,
                plugin_root,
                _plugin_manifest_path(plugin_root, plugin_format),
                normalize,
                root_dir=root_dir,
                root_spec=root_spec,
                surface=surface,
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
            surface=surface,
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
            surface=surface,
        )
    elif parent.kind == "plugin":
        _add_bundled_skills(
            graph,
            parent,
            directory,
            normalize,
            root_dir=root_dir,
            root_spec=root_spec,
            surface=surface,
        )
        _add_bundled_plugin_surfaces(
            graph,
            parent,
            directory,
            normalize,
            root_dir=root_dir,
            root_spec=root_spec,
            surface=surface,
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


def _resolve_plugin_format(
    directory: Path,
    surface: RepoSurface,
    *,
    eval_root: Path | None = None,
    spec: GitIgnoreSpec | None = None,
) -> PluginFormat | None:
    """The single decider of which `PluginFormat` governs `directory`: the
    first candidate in `surface.plugin_formats` **list order** (precedence,
    not filesystem walk order) whose `<manifest_dir>/<manifest_filename>`
    exists on disk, is NOT gitignored (a candidate under `eval_root` that
    `spec` ignores is treated as absent — otherwise a default scan could pick
    a manifest `--include-gitignored` was supposed to exclude), AND whose
    content qualifies per `fmt.detect` (ADR-0053: "first qualifying
    candidate", not first path-shape match — load-bearing once a surface has
    more than one format sharing a directory, e.g. Cursor's
    `.cursor-plugin/plugin.json` beside a root `plugin.json`). `None` if no
    candidate both exists and qualifies.

    `eval_root`/`spec` default to `None` (no gitignore filtering), matching
    endpoint-mode callers (installed artifacts are not repo source).

    This is the one routine both plugin-root discovery (`_find_plugin_roots`)
    and manifest-path re-derivation (`_resolve_plugin_manifest`) call, so the
    two can never disagree about which manifest governs a given root.
    """
    for fmt in surface.plugin_formats:
        manifest = directory / fmt.manifest_dir / fmt.manifest_filename
        if not manifest.is_file():
            continue
        if eval_root is not None and _is_ignored_under(manifest, eval_root, spec):
            continue
        try:
            data = json.loads(manifest.read_text())
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and fmt.detect(data):
            return fmt
    return None


def _plugin_manifest_path(directory: Path, fmt: PluginFormat) -> Path:
    return directory / fmt.manifest_dir / fmt.manifest_filename


def _find_plugin_roots(
    directory: Path, surface: RepoSurface, *, include_gitignored: bool = False
) -> list[tuple[Path, PluginFormat]]:
    """Plugin roots are dirs containing one of `surface.plugin_formats`'
    `<manifest_dir>/<manifest_filename>`, at ANY depth (parity with parse_repo).
    Discovery uses the same gitignore-aware walk as project-skill discovery so
    we skip `node_modules/`, `.git/`, gitignored dirs. Returns each plugin
    root paired with the `PluginFormat` `_resolve_plugin_format` picked for it
    (list-order precedence, not the order the walk happened to see candidate
    files in), sorted by root for determinism.
    """
    spec = None if include_gitignored else load_gitignore_spec(directory)
    candidate_roots: dict[Path, Path] = {}  # resolved -> logical
    for path in iter_unignored_files(directory, spec):
        for fmt in surface.plugin_formats:
            if path.name != fmt.manifest_filename:
                continue
            if fmt.manifest_dir:
                if path.parent.name != fmt.manifest_dir:
                    continue
                root = path.parent.parent
            else:
                # manifest_dir="": the manifest sits directly in the plugin
                # root (e.g. Agent Plugins' root `plugin.json`), not nested
                # one level down.
                root = path.parent
            resolved = root.resolve()
            if resolved not in candidate_roots:
                candidate_roots[resolved] = root
            break
    roots: list[tuple[Path, PluginFormat]] = []
    for root in candidate_roots.values():
        fmt = _resolve_plugin_format(root, surface, eval_root=directory, spec=spec)
        if fmt is not None:
            roots.append((root, fmt))
    return sorted(roots, key=lambda entry: entry[0])


def _attach_mcp_launch_deps(
    graph: Graph,
    scan_root: Path,
    normalize: SourceNormalizer,
    name_index: dict[tuple[str, str], Path],
    *,
    project_root: Path | None = None,
    include_gitignored: bool = False,
    project_root_include_gitignored: bool = False,
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
    # Pre-compute project-root gitignore spec once; used when attaching deps for
    # project-scoped MCPs to match the project name-index filtering (commit 957d909).
    project_root_spec: GitIgnoreSpec | None = None
    if project_root is not None and not project_root_include_gitignored:
        project_root_spec = load_gitignore_spec(project_root)
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
        # Project-scoped MCPs (effective_scan_root is project_root) use the
        # project-root gitignore context, matching project-skills and project
        # name-index filtering. Install-root MCPs use the endpoint-wide context
        # (include_gitignored=True; installed artifacts are never filtered).
        if effective_scan_root is not scan_root:
            eff_include = project_root_include_gitignored
            eff_root = project_root
            eff_spec = project_root_spec
        else:
            eff_include = include_gitignored
            eff_root = root_dir
            eff_spec = root_spec
        before = {e.child for e in graph.edges if e.parent == mcp.key}
        _add_dep_manifest_packages(
            graph,
            mcp,
            resolved,
            normalize,
            include_gitignored=eff_include,
            root_dir=eff_root,
            root_spec=eff_spec,
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
    surface: RepoSurface = CLAUDE_CODE_SURFACE,
    plugin_extra: dict | None = None,
) -> Node | None:
    """Create the plugin node (child of target) and descend into its subtree.

    Reuses `claude_plugin.parse` only to obtain the plugin self-identity ref;
    placement (the plugin → target edge, and which children hang off the
    plugin) is owned here.

    Returns the created plugin node, or `None` when the manifest is malformed
    or yields no self-ref. A `None` return means the directory is NOT an owned
    plugin subtree, so the caller must not exclude it from sibling discovery.
    """
    parsed = _safe_parse(graph, claude_plugin.parse, plugin_manifest)
    self_ref = next((r for r in parsed if _component_type(r) == "plugin"), None)
    if self_ref is None:
        return None
    if plugin_extra:
        # Merged BEFORE the node is created, not after descending. `_add_child`
        # finalizes identity on insert and children inherit the parent's
        # `_identity_namespace`, so a field that decides the plugin's identity
        # (e.g. `marketplace`) has to be present here or the whole subtree
        # finalizes against a namespace-less parent.
        self_ref = replace(self_ref, extra={**(self_ref.extra or {}), **plugin_extra})
    plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
    _add_child(graph, target, plugin_node)
    descend(
        graph,
        plugin_node,
        plugin_root,
        normalize,
        root_dir=root_dir,
        root_spec=root_spec,
        surface=surface,
    )
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
    surface: RepoSurface = CLAUDE_CODE_SURFACE,
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
        if path.name != "SKILL.md" or not _is_project_skill_md(path, directory, surface):
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


def _is_project_skill_md(
    path: Path, root: Path, surface: RepoSurface = CLAUDE_CODE_SURFACE
) -> bool:
    """True iff `path` is a project skill's `SKILL.md` relative to root.

    Claude Code (empty `skill_config_dirs`) is a one-level walk:
    `.../<config_dir>/<project_skills_subdir>/<name>/SKILL.md`. A kind that
    sets `skill_config_dirs` (Codex) means recursive nesting beneath
    `<config_dir>/<project_skills_subdir>/`, matching
    `graph_build_cursor._is_cursor_skill_md`'s any-depth walk — this mirrors
    that so `CODEX_MANIFEST_REGISTRY`'s `**/.codex/skills/**/SKILL.md` glob
    and graph composition agree on what a Codex skill is.
    """
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    if surface.skill_config_dirs:
        for i in range(len(parts) - 2):
            if parts[i] not in surface.skill_config_dirs:
                continue
            if parts[i + 1] in surface.excluded_skill_dirs:
                continue
            if parts[i + 1] != surface.project_skills_subdir:
                continue
            return True
        return False
    # parts == (..., config_dir, project_skills_subdir, "<name>", "SKILL.md")
    return (
        len(parts) >= 4
        and parts[-1] == "SKILL.md"
        and parts[-3] == surface.project_skills_subdir
        and parts[-4] == surface.config_dir
    )


def _add_bundled_skills(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize: SourceNormalizer,
    *,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
    surface: RepoSurface = CLAUDE_CODE_SURFACE,
) -> None:
    """Plugin-bundled skills live at `<plugin-root>/<surface.bundled.skills_dir>/<name>/SKILL.md`,
    or at a custom directory named by the manifest's `"skills"` field — read from
    whichever manifest `surface` resolved for this plugin root, per ADR-0053.

    Path resolution mirrors `claude_plugin_root._parse_bundled_skills`:
    `resolve_within` rejects traversal outside the plugin root, the default
    skills dir is always tried, and a custom dir equal to the default is
    deduped.
    """
    skill_dirs: list[Path] = []
    default_skills = resolve_within(directory, surface.bundled.skills_dir)
    if default_skills is not None and default_skills.is_dir():
        skill_dirs.append(default_skills)
    eval_root, spec = _ignore_context(directory, False, root_dir, root_spec)
    custom_skills = _plugin_custom_skills_field(directory, surface, eval_root=eval_root, spec=spec)
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


def _plugin_custom_skills_field(
    plugin_root: Path,
    surface: RepoSurface,
    *,
    eval_root: Path | None = None,
    spec: GitIgnoreSpec | None = None,
) -> object:
    manifest = _resolve_plugin_manifest(plugin_root, surface, eval_root=eval_root, spec=spec)
    if not manifest.is_file():
        return None
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


def _add_project_skills_from_dir_following_symlinks(
    graph: Graph,
    parent: Node,
    skills_dir: Path,
    *,
    normalize: SourceNormalizer,
    project_root: Path | None = None,
    stamp_provenance: bool = False,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    """Recursive, symlink-following counterpart to `_add_skills_from_dir`, for
    project-skill surfaces where nesting is meaningful (`RepoSurface.skill_config_dirs`
    set — Codex's `.codex/skills/**/SKILL.md`).

    `_add_project_skills`'s own tree walk (`iter_unignored_files`, os.walk-based)
    does not follow directory symlinks at any depth, and `_add_skills_from_dir`
    (the flat, direct-child-only patch for that gap) only resolves symlinks
    among `skills_dir`'s immediate children — so a symlink nested below an
    intermediate real directory (e.g. `skills/team/tool -> /store/tool`) was
    still missed by both. `_iter_skill_subdirs_following_symlinks` is the
    cycle-safe walker already used to close this identical gap for install-root
    direct skills (`_add_direct_endpoint_skills`); reused here rather than
    reimplemented.

    Unlike install-root skills (unfiltered — see `_ignore_context`'s docstring),
    this walk is the project-skill surface, which DOES filter by the project
    root's `.gitignore` (`_seed_shared_endpoint_surfaces` threads
    `root_dir`/`root_spec` for exactly that reason). `root_dir`/`root_spec` are
    also forwarded into `_add_skill_node` so a skill kept by this filter still
    has its own dep manifests filtered on descent, matching `_add_skills_from_dir`.
    """
    if not skills_dir.is_dir():
        return
    eval_root, spec = _ignore_context(skills_dir, False, root_dir, root_spec)
    for skill_subdir in sorted(
        _iter_skill_subdirs_following_symlinks(graph, skills_dir), key=lambda p: str(p)
    ):
        skill_md = skill_subdir / "SKILL.md"
        try:
            is_skill = skill_md.is_file()
        except OSError as exc:
            graph.record_gap(f"could not read {skill_md}: {exc}")
            continue
        if not is_skill:
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
    for ref in _safe_parse(graph, lambda path: claude_skill.parse(path, strict=True), skill_md):
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


def _safe_parse(graph: Graph, parse, manifest: Path) -> list[ComponentRef]:
    """Run a leaf parser, recording and swallowing per-manifest failures.

    These parsers run against arbitrary user repos; one malformed file (bad
    JSON, unreadable bytes) must not abort the whole graph build. This mirrors
    `parse_repo_grouped`'s per-path try/except — descent skips the bad file and
    continues.
    """
    try:
        return parse(manifest)
    except Exception as exc:
        graph.record_gap(f"could not parse {manifest}: {exc}")
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
        for ref in _safe_parse(
            graph, lambda path: _DEP_MANIFEST_PARSERS[filename](path, strict=True), manifest
        ):
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
        for ref in _safe_parse(
            graph, lambda path: _DEP_MANIFEST_PARSERS[filename](path, strict=True), manifest
        ):
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
    if spec is None:
        return False
    # Callers hand this a RESOLVED path (symlink containment is checked before
    # the ignore test), so `eval_root` has to be resolved too or the compare is
    # between different spellings of the same directory — `/var/...` versus
    # `/private/var/...` on macOS, or any checkout reached through a symlink.
    # `relative_to` then raises and the file is silently treated as NOT
    # ignored, which is the permissive direction: a gitignored command or
    # subagent gets composed. Try the literal root first so an unresolved
    # caller keeps working, then the resolved one.
    for base in (eval_root, _resolved_or_none(eval_root)):
        if base is None:
            continue
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        return is_ignored(rel, spec)  # type: ignore[arg-type]
    return False


def _resolved_or_none(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


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
    surface: RepoSurface = CLAUDE_CODE_SURFACE,
) -> None:
    """Repo target-level standalone surfaces: MCP manifests and `<config_dir>`
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
        if path.name in surface.standalone_mcp_filenames:
            for ref in _safe_parse(graph, mcp_json.parse, path):
                if _component_type(ref) != "mcp_server":
                    continue
                node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
                _add_child(graph, parent, node)
            continue
        if path.name == surface.settings_filename and _is_claude_settings_json(
            path, directory, surface
        ):
            for ref in _safe_parse(graph, claude_settings.parse, path):
                if _component_type(ref) != "plugin":
                    continue
                node = Node(key=occurrence_key(ref, normalize), kind="plugin", ref=ref)
                _add_child(graph, parent, node)
            continue
        if path.suffix == ".md":
            kind = _command_agent_kind(path, directory, surface)
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


def _is_claude_settings_json(
    path: Path, root: Path, surface: RepoSurface = CLAUDE_CODE_SURFACE
) -> bool:
    """True iff `path` is `<config_dir>/<settings_filename>` at any depth
    relative to root."""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return False
    settings_rel = f"{surface.config_dir}/{surface.settings_filename}"
    return rel == settings_rel or rel.endswith(f"/{settings_rel}")


def _command_agent_kind(
    path: Path, root: Path, surface: RepoSurface = CLAUDE_CODE_SURFACE
) -> Kind | None:
    """Return `"command"`/`"agent"` if `path` is a `.md` under a
    `<config_dir>/<subdir>/` dir at any depth, else None."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    for subdir, kind in surface.command_agent_surfaces:
        for i in range(len(parts) - 2):
            if parts[i] == surface.config_dir and parts[i + 1] == subdir:
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
    surface: RepoSurface = CLAUDE_CODE_SURFACE,
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
    # Computed up front (not just at the final ref-emission filter below) so
    # a gitignored higher-precedence manifest/MCP candidate never wins format
    # or default-MCP resolution over an unignored lower-precedence one — the
    # same "gitignored candidate hides an unignored one" hazard the command/
    # subagent precedence resolvers guard against.
    eval_root, spec = _ignore_context(plugin_root, False, root_dir, root_spec)
    plugin_data = _plugin_manifest_data(graph, plugin_root, surface, eval_root=eval_root, spec=spec)
    plugin_manifest_path = _resolve_plugin_manifest(
        plugin_root, surface, eval_root=eval_root, spec=spec
    )
    # `field`, not `surface`: this loop is over manifest field NAMES and would
    # otherwise shadow the `RepoSurface` parameter it sits beside.
    for field in ("skills", "commands", "agents"):
        if field not in plugin_data:
            continue
        declared_path = plugin_data[field]
        resolved = (
            resolve_within(plugin_root, declared_path) if isinstance(declared_path, str) else None
        )
        if resolved is None or not resolved.is_dir():
            graph.record_gap(
                f"could not parse {plugin_manifest_path}: {field} must name an available directory"
            )

    refs: list[ComponentRef] = []
    manifest_refs = _parse_manifest_refs(
        plugin_data,
        plugin_json_path=plugin_manifest_path,
        plugin_root=plugin_root,
        warnings=graph.warnings,
    )
    refs.extend(manifest_refs)
    # The one deliberate crossing of the placement/content boundary (ADR-0053):
    # the bundled MCP filename(s) are placement data (`surface.bundled.mcp_filenames`)
    # threaded into a leaf parser.
    refs.extend(
        _parse_default_mcp(
            plugin_root,
            manifest_refs,
            warnings=graph.warnings,
            mcp_filenames=surface.bundled.mcp_filenames,
            eval_root=eval_root,
            spec=spec,
        )
    )
    refs.extend(
        _parse_bundled_hooks(
            plugin_root,
            plugin_data,
            plugin_name,
            warnings=graph.warnings,
            plugin_json_path=plugin_manifest_path,
            hooks_filename=surface.bundled.hooks_filename,
        )
    )
    refs.extend(
        _parse_bundled_command_agents(
            plugin_root,
            plugin_data,
            plugin_name,
            warnings=graph.warnings,
            commands_dir=surface.bundled.commands_dir,
            agents_dir=surface.bundled.agents_dir,
        )
    )
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
    # (`eval_root`/`spec` computed once, above, before manifest/MCP resolution.)
    agent_nodes_by_source: dict[str, Node] = {}
    for ref in refs:
        component_type = _component_type(ref)
        if not isinstance(component_type, str) or component_type not in {"agent", "command"}:
            continue
        if ref.source_manifest and _is_ignored_under(Path(ref.source_manifest), eval_root, spec):
            continue
        node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
        _add_child(graph, plugin_node, node)
        if component_type == "agent" and ref.source_manifest:
            agent_nodes_by_source[ref.source_manifest] = node
    for ref in refs:
        component_type = _component_type(ref)
        if not isinstance(component_type, str) or component_type in {"agent", "command"}:
            continue
        if ref.source_manifest and _is_ignored_under(Path(ref.source_manifest), eval_root, spec):
            continue
        node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
        parent = agent_nodes_by_source.get(ref.source_manifest or "", plugin_node)
        _add_child(graph, parent, node)


def _resolve_plugin_manifest(
    plugin_root: Path,
    surface: RepoSurface,
    *,
    eval_root: Path | None = None,
    spec: GitIgnoreSpec | None = None,
) -> Path:
    """The manifest path for an already-realized plugin root, via the same
    `_resolve_plugin_format` decision `_find_plugin_roots` uses — including
    the same `eval_root`/`spec` gitignore filtering, so the two can never
    disagree about which format governs a root even when a higher-precedence
    candidate is gitignored. Falls back to the first candidate's path when
    none is present on disk (parity with the old unconditional
    `.claude-plugin/plugin.json` path, whose read is guarded by callers)."""
    fmt = (
        _resolve_plugin_format(plugin_root, surface, eval_root=eval_root, spec=spec)
        or surface.plugin_formats[0]
    )
    return _plugin_manifest_path(plugin_root, fmt)


def _plugin_manifest_data(
    graph: Graph,
    plugin_root: Path,
    surface: RepoSurface,
    *,
    eval_root: Path | None = None,
    spec: GitIgnoreSpec | None = None,
) -> dict:
    if (
        _resolve_plugin_format(plugin_root, surface, eval_root=eval_root, spec=spec) is None
        and surface.manifest_optional
    ):
        # No candidate manifest resolved AND this kind permits that: Cursor's
        # marketplace cache ships bundles with no manifest at all, which load
        # entirely by folder discovery (docs/specs/cursor-agent-kind.md,
        # Plugins). Warning would report every such bundle as unparseable, and
        # policy mode escalates that to a hard error. Claude Code's plugins are
        # named by an install lockfile that points at a manifest, so a missing
        # one there stays a real defect and still warns below.
        return {}
    manifest = _resolve_plugin_manifest(plugin_root, surface, eval_root=eval_root, spec=spec)
    if not manifest.is_file():
        graph.record_gap(f"could not parse {manifest}: file is unavailable")
        return {}
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        graph.record_gap(f"could not parse {manifest}: {exc}")
        return {}
    if not isinstance(data, dict):
        graph.record_gap(f"plugin manifest {manifest} must contain an object")
        return {}
    return data


# Public aliases for the shared graph-construction primitives a second kind's
# composer needs (ADR-0053): a cross-module private import is never the
# contract. Renaming one of these is an interface change, not a refactor.
add_child = _add_child
make_normalizer = _make_normalizer
add_dep_manifest_packages = _add_dep_manifest_packages
find_plugin_roots = _find_plugin_roots
descend_into_plugin = _descend_into_plugin
add_skill_node = _add_skill_node
ignore_context = _ignore_context
is_ignored_under = _is_ignored_under
component_type_of = _component_type
safe_parse = _safe_parse
same_path = _same_path
resolve_plugin_format = _resolve_plugin_format
plugin_manifest_path = _plugin_manifest_path


# --- Codex composition (plan 043, ADR-0055/0057) ---------------------------
#
# Codex's repo surface is expressible in ADR-0053's existing `RepoSurface`, so
# repo mode reuses `descend` rather than forking a walker — the parameterization
# working as intended for a third kind. Two surfaces sit outside the descriptor
# and are added alongside the walk rather than by widening it:
#
# - `<project>/.codex/hooks.json`. Claude Code has no repo-mode standalone hooks
#   surface (its own spec says so) and Cursor deferred one, so `RepoSurface`
#   has no field to name it. One kind needing it is not yet evidence the
#   descriptor is wrong; a second would be.
# - MCP servers, which live inside `.codex/config.toml` as one TOML table among
#   four. `scoped_mcp_rels` names a path but assumes an MCP-shaped manifest;
#   Codex's is a config file that happens to carry servers.


def _add_codex_declared_config_mcps(
    graph: Graph,
    target: Node,
    scan_root: Path,
    normalize: SourceNormalizer,
    *,
    include_gitignored: bool,
    root_spec: GitIgnoreSpec | None,
    realized_plugin_roots: list[Path],
) -> None:
    """MCP servers from every `.codex/config.toml` in the tree.

    Content beneath an already-realized plugin root belongs to the plugin
    branch, not the tree walk (single-parent invariant, same rule
    `CODEX_SURFACE.excludes_plugin_owned_content` applies to declared
    evidence and registry parse-count accounting) — otherwise a fixture like
    a plugin's own `examples/.codex/config.toml` would add its MCP servers
    directly under the target.
    """
    from tools.graph_build_cursor import is_owned_by_realized_plugin

    for config_path in sorted(scan_root.rglob(".codex/config.toml")):
        if not include_gitignored and _is_ignored_under(config_path, scan_root, root_spec):
            continue
        if is_owned_by_realized_plugin(config_path, realized_plugin_roots, CODEX_SURFACE):
            continue
        refs = _safe_parse(graph, codex_config.parse, config_path)
        for ref in refs:
            node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
            _add_child(graph, target, node)


def _add_codex_declared_config_hooks(
    graph: Graph,
    target: Node,
    scan_root: Path,
    normalize: SourceNormalizer,
    *,
    include_gitignored: bool,
    root_spec: GitIgnoreSpec | None,
    realized_plugin_roots: list[Path],
) -> None:
    """Inline `[hooks]` tables from every `.codex/config.toml` in the tree.

    `_add_codex_declared_hooks` below reads the sidecar `.codex/hooks.json`;
    `config.toml` is a documented alternative form of the same envelope
    (`_seed_codex_hooks`'s docstring), and a project declaring hooks only this
    way — never a `hooks.json` — had them silently absent from the declared
    graph. Same walk, exclusion, and scope as the sidecar form; only the
    source file and reader differ.
    """
    from tools.graph_build_cursor import is_owned_by_realized_plugin

    for config_path in sorted(scan_root.rglob(".codex/config.toml")):
        if not include_gitignored and _is_ignored_under(config_path, scan_root, root_spec):
            continue
        if is_owned_by_realized_plugin(config_path, realized_plugin_roots, CODEX_SURFACE):
            continue
        try:
            config = codex_config.load_config(config_path)
            _gap_malformed_codex_surfaces(graph, config_path, config)
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            graph.record_gap(f"could not parse {config_path}: {exc}")
            continue
        if not config.hooks:
            continue
        try:
            hook_refs = hooks_json.parse_settings_hooks(
                config_path, config.hooks, scope="project", strict=True
            )
        except ValueError as exc:
            graph.record_gap(f"could not parse {config_path} hooks: {exc}")
            continue
        for ref in hook_refs:
            component_type = _component_type(ref)
            if not isinstance(component_type, str):
                continue
            node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
            _add_child(graph, target, node)


def _add_codex_declared_hooks(
    graph: Graph,
    target: Node,
    scan_root: Path,
    normalize: SourceNormalizer,
    *,
    include_gitignored: bool,
    root_spec: GitIgnoreSpec | None,
    realized_plugin_roots: list[Path],
) -> None:
    """Standalone `.codex/hooks.json`, project scope.

    The envelope and event names are Claude Code's — PascalCase, same shape —
    so `hooks_json` parses it with no Codex-specific handling. Plugin-owned
    content is excluded for the same reason as the config-MCP walk above.
    """
    from tools.graph_build_cursor import is_owned_by_realized_plugin

    for hooks_path in sorted(scan_root.rglob(".codex/hooks.json")):
        if not include_gitignored and _is_ignored_under(hooks_path, scan_root, root_spec):
            continue
        if is_owned_by_realized_plugin(hooks_path, realized_plugin_roots, CODEX_SURFACE):
            continue
        refs = _safe_parse(
            graph,
            lambda path: hooks_json.parse_standalone_hooks(path, scope="project", strict=True),
            hooks_path,
        )
        for ref in refs:
            component_type = _component_type(ref)
            if not isinstance(component_type, str):
                continue
            node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
            _add_child(graph, target, node)


def build_codex_declared_graph(
    agent,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Repo-mode composition for one Codex `AgentInstance`.

    `agent` is duck-typed rather than imported, so this module keeps ADR-0044's
    one-way dependency: `agent_kinds` may import `graph_build`, never the
    reverse.
    """
    scan_root = Path(agent.scan_root)
    root = Node(key=agent.bom_ref, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = _make_normalizer("repo", scan_root, scan_root, None, agent.root_label)
    root_spec = None if include_gitignored else load_gitignore_spec(scan_root)

    descend(
        graph,
        root,
        scan_root,
        normalize,
        include_gitignored=include_gitignored,
        root_dir=scan_root,
        root_spec=root_spec,
        surface=CODEX_SURFACE,
    )
    from tools.graph_build_cursor import realized_plugin_roots as find_realized_plugin_roots

    realized_roots = find_realized_plugin_roots(
        scan_root, CODEX_SURFACE, include_gitignored=include_gitignored
    )
    _add_codex_declared_config_mcps(
        graph,
        root,
        scan_root,
        normalize,
        include_gitignored=include_gitignored,
        root_spec=root_spec,
        realized_plugin_roots=realized_roots,
    )
    _add_codex_declared_config_hooks(
        graph,
        root,
        scan_root,
        normalize,
        include_gitignored=include_gitignored,
        root_spec=root_spec,
        realized_plugin_roots=realized_roots,
    )
    _add_codex_declared_hooks(
        graph,
        root,
        scan_root,
        normalize,
        include_gitignored=include_gitignored,
        root_spec=root_spec,
        realized_plugin_roots=realized_roots,
    )

    return finalize_graph(
        graph,
        scan_root,
        normalize,
        include_gitignored=include_gitignored,
        attach_include_gitignored=include_gitignored,
        root_dir=scan_root,
        root_spec=root_spec,
        warnings=warnings,
    )


# Codex marks its vendor-owned built-in skill root with this zero-byte file.
# Excluding by marker rather than by directory name is deliberate: Cursor
# filters the same six built-ins by a hardcoded name list its own spec flags as
# drift-prone, and a name list stops working the moment the directory is
# renamed.
_CODEX_SYSTEM_SKILLS_MARKER = ".codex-system-skills.marker"


def _codex_system_skill_roots(config_root: Path) -> list[Path]:
    """Directories under `<root>/skills/` that carry the built-in marker."""
    skills_dir = config_root / "skills"
    if not skills_dir.is_dir():
        return []
    roots: list[Path] = []
    try:
        entries = sorted(skills_dir.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.is_dir() and (entry / _CODEX_SYSTEM_SKILLS_MARKER).exists():
            roots.append(entry.resolve())
    return roots


def _seed_codex_shared_agent_skills(
    graph: Graph, target: Node, normalize: SourceNormalizer
) -> None:
    """`$HOME/.agents/skills` — the user-scope half of the cross-tool
    convention directory.

    Codex's published skills reference lists repository `.agents/skills` and
    `$HOME/.agents/skills` among its discovery locations. The repository half
    is reached in repo mode through `CODEX_SURFACE.skill_config_dirs`; this is
    the endpoint half, which no config root contains because it hangs off the
    home directory rather than `$CODEX_HOME`.

    Home-scoped like Cursor's own compat roots, and for the same reason: a
    `--config-dir` override moves Codex's root, not the user's home.

    `/etc/codex/skills` (the admin location) is deliberately not read here —
    it is the same class of administrator-distributed surface as
    `managed_config.toml` and is recorded as deferred in the spec.
    """
    _add_direct_endpoint_skills(graph, target, Path.home() / ".agents" / "skills", normalize)


def _seed_codex_subagents(
    graph: Graph,
    target: Node,
    config_root: Path,
    normalize: SourceNormalizer,
    project_root: Path | None = None,
) -> None:
    """Codex subagents, from BOTH declaration forms.

    1. **A file in the agents directory** — `<root>/agents/*.toml`.
    2. **A config-declared role** — `[agents."<role>"] config_file = "..."` in
       any active config layer. The referenced file may live anywhere:
       "Path to a TOML config layer for that role; relative paths resolve from
       the config file that declares the role"
       (developers.openai.com/codex/config-reference).

    Form 2 was missing, so a role whose file sits outside `agents/` was
    reported as no subagent at all. An earlier spec line claimed subagents were
    directory-discovered only; its evidence was that the audited binary
    contains no `.codex/agents` string literal, which does not follow — a
    program that builds a path from components has no such literal. Corrected
    against the published configuration reference.

    Both forms are read rather than one, because the same reference documents
    role files in an agents directory as well. Where they overlap, `_add_child`
    dedupes on the occurrence key.

    The role identity is the **table key**, not the referenced file's own
    `name`: the key is what selects the role.
    """
    agents_dir = config_root / "agents"
    if agents_dir.is_dir():
        for path in sorted(agents_dir.glob("*.toml")):
            for ref in _safe_parse(graph, codex_agent.parse, path):
                node = Node(key=occurrence_key(ref, normalize), kind="agent", ref=ref)
                _add_child(graph, target, node)

    for layer in codex_config_layers(config_root, project_root):
        try:
            config = codex_config.load_config(layer.path)
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            graph.record_gap(f"could not parse {layer.path}: {exc}")
            continue
        _gap_malformed_codex_surfaces(graph, layer.path, config)
        for role in config.agents.values():
            _seed_codex_config_role(graph, target, layer.path, role, normalize)


def _seed_codex_config_role(
    graph: Graph,
    target: Node,
    declaring_config: Path,
    role,
    normalize: SourceNormalizer,
) -> None:
    """One `[agents."<role>"]` declaration.

    A role naming a `config_file` that is missing is a component we know exists
    and cannot read, so it lowers coverage — the reference says the path "is
    validated at load time and must point to an existing file", meaning Codex
    itself treats this as an error rather than an absent role.
    """
    if role.config_file is None:
        # A role with no file still declares a subagent; its instructions just
        # come from the parent session. Emit it from the table alone.
        ref = ComponentRef(
            name=role.name,
            component_identity=f"claude-agent/{role.name}",
            source_manifest=str(declaring_config),
            source_locator=f'$.agents."{role.name}"',
            extra={
                "scope_owner": None,
                "component_type": "agent",
                **({"description": role.description} if role.description else {}),
            },
        )
        _add_child(graph, target, Node(occurrence_key(ref, normalize), "agent", ref))
        return

    # "relative paths resolve from the config file that declares the role"
    referenced = Path(role.config_file)
    if not referenced.is_absolute():
        referenced = declaring_config.parent / referenced
    if not referenced.is_file():
        graph.record_gap(
            f"could not parse {referenced}: agents.{role.name} config_file is unavailable"
        )
        return

    for ref in _safe_parse(graph, codex_agent.parse, referenced):
        # The table key selects the role, so it is the identity — the
        # referenced file's own `name` is free to disagree.
        renamed = replace(
            ref,
            name=role.name,
            component_identity=f"claude-agent/{role.name}",
        )
        _add_child(graph, target, Node(occurrence_key(renamed, normalize), "agent", renamed))


def _emit_codex_config_mcp_servers(
    graph: Graph, target: Node, config_path: Path, normalize: SourceNormalizer
) -> None:
    """Parse one config layer and add each of its MCP servers as an additive
    occurrence (never merged by name) — the emission `_seed_codex_profile_mcp_servers`
    and a profile-only-trusted project layer (`_seed_codex_mcp_servers`) share.
    """
    for ref in _safe_parse(graph, lambda path: codex_config.parse(path, strict=True), config_path):
        node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
        _add_child(graph, target, node)


def _seed_codex_mcp_servers(
    graph: Graph,
    target: Node,
    config_root: Path,
    project_root: Path | None,
    normalize: SourceNormalizer,
    *,
    warnings: list[str] | None = None,
) -> None:
    """Codex's MCP servers, from `config.toml` rather than JSON settings layers.

    Forked from `_seed_remote_mcps` (ADR-0057): that function reads Claude
    Code's settings layers and `.mcp.json`; Codex's servers are a TOML table in
    one config file, with a project file layered over the user one.

    Project entries win, matching `_seed_endpoint`'s own precedence rule — but
    only when `codex_project_trusted_unconditionally` says the project is
    trusted by the **base** config, the same distinction `_seed_cache_plugins`
    draws for plugin enablement. When trust instead comes from a profile only,
    the project's `.codex/config.toml` is active exclusively when that profile
    is selected; a plain, no-profile invocation still runs the base server, so
    replacing it by name would hide a reachable occurrence. In that case the
    project's servers join the profile servers below as additive occurrences
    instead.
    """
    layers = codex_config_layers(config_root, project_root)
    project_overrides = any(layer.kind == "project" and layer.overrides for layer in layers)

    # Base always merges by name; the project layer only joins this
    # override-by-name merge when its trust is unconditional. Profiles (and a
    # profile-trusted project, below) are handled separately because their
    # semantics differ: they are alternates, not an override of the base.
    override_kinds = {"base", "project"} if project_overrides else {"base"}
    by_name: dict[str, ComponentRef] = {}
    for layer in [layer for layer in layers if layer.kind in override_kinds]:
        for ref in _safe_parse(
            graph, lambda path: codex_config.parse(path, strict=True), layer.path
        ):
            name = _codex_server_name(ref)
            if name is not None:
                by_name[name] = ref

    for ref in by_name.values():
        node = Node(key=occurrence_key(ref, normalize), kind="mcp_server", ref=ref)
        _add_child(graph, target, node)

    _seed_codex_profile_mcp_servers(graph, target, config_root, normalize)

    if project_root is not None and not project_overrides:
        for layer in layers:
            if layer.kind == "project":
                _emit_codex_config_mcp_servers(graph, target, layer.path, normalize)


@dataclass(frozen=True)
class CodexConfigLayer:
    """One config file that can contribute to an installed Codex agent."""

    path: Path
    scope: str  # "user" | "project"
    kind: str  # "base" | "profile" | "project"
    # Whether this layer is active on EVERY invocation, and may therefore
    # override the base by name. False for a profile (only one is selected, and
    # the selection leaves no trace on disk) and for a project whose trust is
    # itself declared only in a profile — a base-enabled component is still
    # reachable through a plain, no-profile run, so overriding it would hide a
    # reachable occurrence.
    #
    # Carried here rather than recomputed per surface: each surface deriving
    # its own answer is what produced a round of findings where MCP servers,
    # cached plugins, and hooks each disagreed about the profile-only-trust
    # case.
    overrides: bool = True


def _gap_malformed_codex_surfaces(graph: Graph, path: Path, config) -> None:
    """Record a coverage gap per present-but-malformed config surface.

    A surface of the wrong TOML type declares components we cannot read, so it
    lowers coverage exactly as an unparseable file would. Without this the
    surface reads as absent and the BOM claims `complete` while silently
    dropping every component it declared.
    """
    for surface in config.malformed:
        graph.record_gap(f"could not parse {path}: {surface} must be a table")


def codex_trusted_projects(config_root: Path) -> set[str]:
    """Project paths Codex records as trusted, across every layer that can
    carry the record.

    Unions the base config and every profile: `codex -p <name>` layers a
    profile over the base, so a directory marked trusted in a profile only is
    still trusted when that profile is selected — and which profile is selected
    leaves no trace on disk.
    """
    trusted: set[str] = set()
    for path in [config_root / "config.toml", *sorted(config_root.glob("*.config.toml"))]:
        if not path.is_file():
            continue
        try:
            config = codex_config.load_config(path)
        except Exception:  # noqa: BLE001 - a broken layer is a scan gap, not a crash
            continue
        trusted.update(p.path for p in config.projects.values() if p.trust_level == "trusted")
    return trusted


def codex_project_trusted_unconditionally(config_root: Path, project_root: Path) -> bool:
    """Whether the project is trusted by the **base** `config.toml` alone.

    Distinct from `codex_trusted_projects`, which unions trust records across
    every profile: a trust record that exists only in a profile's
    `<name>.config.toml` is in effect only when that specific profile is
    selected, not on every invocation. `_seed_cache_plugins` needs this
    distinction — the project layer's plugin declarations are safe to treat as
    a full override only when the project is active no matter which profile
    (if any) is selected; when trust itself is profile-dependent, a
    base-enabled plugin is still reachable through a plain, no-profile
    invocation, and an override would incorrectly hide that.
    """
    path = config_root / "config.toml"
    if not path.is_file():
        return False
    try:
        config = codex_config.load_config(path)
    except Exception:  # noqa: BLE001 - a broken base layer is a scan gap, not a crash
        return False
    trusted = {p.path for p in config.projects.values() if p.trust_level == "trusted"}
    resolved = str(project_root.resolve())
    return resolved in trusted or str(project_root) in trusted


def codex_config_layers(
    config_root: Path, project_root: Path | None = None
) -> list[CodexConfigLayer]:
    """Every config layer that can be active for this endpoint, in precedence
    order: base, then profiles, then the project.

    **One definition, read by every Codex surface.** Each surface previously
    built its own layer list, and each missed a different layer — hooks skipped
    profiles and the project, project trust skipped profiles, plugins read only
    the base. That is why successive review rounds found the same class of bug
    on a different surface each time; the layer set is the thing that was
    duplicated, so it is the thing that is now shared. Merge semantics stay
    with the caller, because they genuinely differ: MCP servers merge by name
    with the project winning, while profile servers are additive occurrences.

    Every profile is returned rather than an active one — the `-p` selection is
    an invocation flag that leaves nothing on disk, so the union
    over-approximates, which is the safe direction.

    The project layer is **trust-gated**: Codex ignores a project's config
    until the directory is trusted, so composing it unconditionally would
    report servers, hooks, and plugins the runtime does not load.
    """
    out = [CodexConfigLayer(config_root / "config.toml", "user", "base")]
    out.extend(
        CodexConfigLayer(path, "user", "profile", overrides=False)
        for path in sorted(config_root.glob("*.config.toml"))
    )
    if project_root is not None:
        resolved = str(project_root.resolve())
        if resolved in codex_trusted_projects(config_root) or str(
            project_root
        ) in codex_trusted_projects(config_root):
            out.append(
                CodexConfigLayer(
                    project_root / ".codex" / "config.toml",
                    "project",
                    "project",
                    overrides=codex_project_trusted_unconditionally(config_root, project_root),
                )
            )
    return [layer for layer in out if layer.path.is_file()]


def _codex_server_name(ref: ComponentRef) -> str | None:
    component_path = (ref.extra or {}).get("component_path") or [{}]
    name = component_path[0].get("name")
    return name if isinstance(name, str) else None


def _seed_codex_profile_mcp_servers(
    graph: Graph, target: Node, config_root: Path, normalize: SourceNormalizer
) -> None:
    """MCP servers declared in a config profile, `<root>/<name>.config.toml`.

    `codex -p <name>` layers that file over the base config, and it carries the
    same schema — verified by running `codex -p work mcp list` against a
    fixture root and seeing the profile's server listed alongside the base
    one. A scan that read only `config.toml` would miss every server a profile
    adds, which is a component gap, not a settings one.

    Every profile is read, not just an active one: which profile is selected is
    an invocation-time flag that leaves no trace on disk. Reporting the union
    over-approximates, which is the safe direction for a security tool and the
    direction this project takes elsewhere.

    Profile servers are additive occurrences rather than merged by name. Two
    profiles declaring the same server differently are two things the agent
    could run, and collapsing them to one would hide whichever lost.
    """
    for layer in codex_config_layers(config_root):
        if layer.kind == "profile":
            _emit_codex_config_mcp_servers(graph, target, layer.path, normalize)


def _seed_codex_hooks(
    graph: Graph,
    target: Node,
    config_root: Path,
    project_root: Path | None,
    normalize: SourceNormalizer,
) -> None:
    """Inline `[hooks]` tables across every active Codex config layer.

    `CODEX_ENDPOINT.seeds_hooks` is `False` because that flag gates the
    *shared* settings.json-layer walk (`_seed_shared_endpoint_surfaces`),
    which Codex has no counterpart for. This is a separate, Codex-owned
    surface — same fork rationale as `_seed_codex_mcp_servers` — reading the
    TOML table's own envelope, which the docs describe as the inline
    equivalent of `hooks.json`'s `{event: [...]}` shape, so composition reuses
    `hooks_json.parse_settings_hooks` rather than a second implementation.

    Unlike MCP servers, hooks fire from every active layer rather than the
    last one to declare a given name (a base-only read silently dropped a
    profile's or a trusted project's own `[hooks]` table), so this unions the
    base config, every `<name>.config.toml` profile — the same layer set
    `_seed_codex_profile_mcp_servers` reads for servers — and the project's
    `.codex/config.toml`, the same project layer `_seed_codex_mcp_servers`
    already reads unconditionally for servers.
    """
    for layer in codex_config_layers(config_root, project_root):
        _seed_codex_hooks_from_layer(graph, target, layer.path, layer.scope, normalize)


def _seed_codex_hooks_from_layer(
    graph: Graph, target: Node, config_path: Path, scope: str, normalize: SourceNormalizer
) -> None:
    if not config_path.is_file():
        return
    try:
        config = codex_config.load_config(config_path)
        _gap_malformed_codex_surfaces(graph, config_path, config)
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal
        graph.record_gap(f"could not parse {config_path}: {exc}")
        return
    if not config.hooks:
        return
    try:
        hook_refs = hooks_json.parse_settings_hooks(
            config_path, config.hooks, scope=scope, strict=True
        )
    except ValueError as exc:
        graph.record_gap(f"could not parse {config_path} hooks: {exc}")
        return
    for ref in hook_refs:
        component_type = _component_type(ref)
        if not isinstance(component_type, str):
            continue
        node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
        _add_child(graph, target, node)


def _seed_codex_standalone_hooks_file(
    graph: Graph, target: Node, hooks_path: Path, scope: str, normalize: SourceNormalizer
) -> None:
    if not hooks_path.is_file():
        return
    for ref in _safe_parse(
        graph,
        lambda path: hooks_json.parse_standalone_hooks(path, scope=scope, strict=True),
        hooks_path,
    ):
        component_type = _component_type(ref)
        if not isinstance(component_type, str):
            continue
        node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
        _add_child(graph, target, node)


def _seed_codex_standalone_hooks(
    graph: Graph,
    target: Node,
    config_root: Path,
    project_root: Path | None,
    normalize: SourceNormalizer,
) -> None:
    """Standalone `hooks.json`, at the user root and (if trusted) the project.

    Distinct from `_seed_codex_hooks` above, which reads the inline `[hooks]`
    table in `config.toml`: Codex's own docs list `$CODEX_HOME/hooks.json` and
    `<project>/.codex/hooks.json` as two more scopes that load unconditionally
    alongside every `config.toml` layer, additively — "higher-precedence config
    layers don't replace lower-precedence hooks" — never overriding one
    another. Endpoint mode previously read neither sidecar, only the inline
    TOML form, so a Codex endpoint configured this way was silently
    under-reported.

    The project file is trust-gated for the same reason `codex_config_layers`
    gates the project config layer: Codex ignores an untrusted project's
    `.codex/` directory outright.
    """
    _seed_codex_standalone_hooks_file(graph, target, config_root / "hooks.json", "user", normalize)
    if project_root is None:
        return
    trusted_paths = codex_trusted_projects(config_root)
    resolved = str(project_root.resolve())
    if resolved not in trusted_paths and str(project_root) not in trusted_paths:
        return
    _seed_codex_standalone_hooks_file(
        graph, target, project_root / ".codex" / "hooks.json", "project", normalize
    )


def _sorted_subdirs(graph: Graph, directory: Path) -> list[Path]:
    """Subdirectories of `directory`, sorted, tolerating an unreadable directory.

    `iterdir()` raises `PermissionError` (a subclass of `OSError`) rather than
    silently returning nothing, and a partial or restrictively-permissioned
    plugin install (e.g. `plugins/cache/<marketplace>/`) must degrade this one
    subtree to a coverage gap rather than aborting the whole endpoint scan —
    same reasoning as `_iter_skill_subdirs_following_symlinks`'s own guarded
    `iterdir()`.
    """
    try:
        entries = sorted(directory.iterdir())
    except OSError as exc:
        graph.record_gap(f"could not list {directory}: {exc}")
        return []
    return [entry for entry in entries if entry.is_dir()]


def _seed_cache_plugins(
    graph: Graph,
    target: Node,
    config_root: Path,
    project_root: Path | None,
    normalize: SourceNormalizer,
    *,
    warnings: list[str] | None = None,
) -> None:
    """Codex's installed plugins, walking the **cache** as the traversal root.

    Forked from `_seed_active_plugins` (ADR-0057), and the walk order is the
    reason. Claude Code iterates its enable map and emits a node only for a
    `True` entry, so a disabled plugin is invisible to it. ADR-0055 requires
    Codex inventory everything installed and record `enabled` alongside — which
    is only expressible by walking the cache first and consulting the enable map
    second. That is a different traversal over a different source, not the same
    procedure with different names.

    Every mismatch a real endpoint can show is reconciled explicitly rather
    than silently resolved; see the warnings below.

    Enable state and marketplace registration are merged across every active
    layer `codex_config_layers` returns for this endpoint — base, every
    `<name>.config.toml` profile, and the trusted project's `.codex/config.toml`
    — the same layer set `_seed_codex_profile_mcp_servers` and `_seed_codex_hooks`
    already read, in the same precedence order, because `[plugins.*]` and
    `[marketplaces.*]` are ordinary tables in any of those files, not a
    base/profile-only surface.

    The two layer kinds merge differently, matching Codex's own config
    precedence docs (`developers.openai.com/codex/config-advanced`: project
    config is a distinct, higher-precedence layer over the user config, not an
    alternate). Profiles union with base by OR: which profile `-p` selects is
    an invocation-time flag left with no trace on disk, so a plugin enabled
    only by *some* profile is still one profile-switch from active, and there
    is no way to know which profile, if any, is the selected one — the same
    reasoning `_seed_codex_profile_mcp_servers` uses for over-approximating
    towards active. The trusted project layer only gets that same override
    treatment when `codex_project_trusted_unconditionally` says trust comes
    from the base config: only then is the project active on *every*
    invocation regardless of profile, so its declarations — including
    replacing an earlier `enabled = true` with `false` — safely override, the
    same "project entries win" precedence `_seed_codex_mcp_servers` already
    applies to servers. When trust instead comes from a profile only, the
    project is active exclusively when that profile is selected; a plain,
    no-profile invocation never loads it, so its plugin table joins the same
    OR-union as profiles rather than overriding — an explicit project
    `enabled = false` must not erase a base `enabled = true` that a
    no-profile invocation can still reach.
    """
    cache_root = config_root / "plugins" / "cache"
    plugins: dict[tuple[str | None, str], codex_config.PluginEntry] = {}
    marketplaces: dict[str, codex_config.MarketplaceEntry] = {}
    all_layers = codex_config_layers(config_root, project_root)
    project_overrides = any(layer.kind == "project" and layer.overrides for layer in all_layers)
    for layer in all_layers:
        try:
            layer_config = codex_config.load_config(layer.path)
            _gap_malformed_codex_surfaces(graph, layer.path, layer_config)
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            graph.record_gap(f"could not parse {layer.path}: {exc}")
            continue
        marketplaces.update(layer_config.marketplaces)
        if layer.kind == "project" and project_overrides:
            plugins.update(layer_config.plugins)
        else:
            for key, entry in layer_config.plugins.items():
                if key not in plugins or entry.enabled:
                    plugins[key] = entry

    seen: set[tuple[str | None, str]] = set()
    if cache_root.is_dir():
        for marketplace_dir in _sorted_subdirs(graph, cache_root):
            marketplace = marketplace_dir.name
            for plugin_dir in _sorted_subdirs(graph, marketplace_dir):
                name = plugin_dir.name
                for version_dir in _sorted_subdirs(graph, plugin_dir):
                    seen.add((marketplace, name))
                    _realize_codex_plugin(
                        graph,
                        target,
                        version_dir,
                        name=name,
                        marketplace=marketplace,
                        version=version_dir.name,
                        plugins=plugins,
                        marketplaces=marketplaces,
                        normalize=normalize,
                    )

    # An enable-map entry naming a plugin with nothing on disk is not a node —
    # there is no artifact to inventory — but it is a real discrepancy.
    for (marketplace, name), entry in sorted(
        plugins.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])
    ):
        if (marketplace, name) not in seen:
            key = f"{name}@{marketplace}" if marketplace else name
            graph.record_gap(f"plugin {key} is configured but missing from plugins/cache")
        _ = entry

    _record_codex_rules_coverage(graph, config_root)


# Codex names a locally-sourced bundle's cache directory `local` instead of a
# version — the same word Claude Code's install lockfile uses for
# `scope: "local"`. It is a cache-layout marker, so emitting it as a version
# would assert one the plugin does not have, and advisory matching on the
# literal string "local" is meaningless.
_CODEX_NON_VERSION_SEGMENTS = frozenset({"local"})


def _codex_plugin_version(version_dir: Path, surface: RepoSurface) -> str | None:
    """The plugin's version: the manifest's own field, else the cache segment.

    The manifest is the authority — the directory name is where the cache
    happened to put the bundle. Falling back to the segment keeps versions for
    the ordinary `<name>/<version>/` layout, and dropping the known layout
    markers keeps a path artifact out of the BOM.
    """
    fmt = _resolve_plugin_format(version_dir, surface)
    manifest = _plugin_manifest_path(version_dir, fmt) if fmt is not None else None
    if manifest is not None and manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            declared = data.get("version")
            if isinstance(declared, str) and declared:
                return declared
    segment = version_dir.name
    return None if segment in _CODEX_NON_VERSION_SEGMENTS else segment


def _realize_codex_plugin(
    graph: Graph,
    target: Node,
    version_dir: Path,
    *,
    name: str,
    marketplace: str,
    version: str,
    plugins: dict,
    marketplaces: dict,
    normalize: SourceNormalizer,
) -> None:
    entry = plugins.get((marketplace, name))
    if entry is None:
        graph.warnings.append(f"plugin {name}@{marketplace} is cached but has no enable-map record")
    enabled = True if entry is None else entry.enabled

    # A cache-path segment is NOT provenance. Marketplace-qualified identity is
    # granted only when `[marketplaces.*]` records the registry the bundle was
    # resolved from; otherwise the segment is just a directory name someone
    # could have created, and minting `plugin/{segment}/{name}` from it would
    # back a real cross-BOM identity with nothing.
    registered = marketplaces.get(marketplace)
    extra: dict = {
        "component_type": "plugin",
        "component_path": [{"type": "plugin", "name": name}],
        "declared_by": {"kind": "manifest", "path": str(version_dir)},
        "installPath": str(version_dir),
        "enabled": enabled,
    }
    if registered is not None:
        extra["marketplace"] = marketplace
        if registered.last_revision:
            extra["last_revision"] = registered.last_revision
        if registered.source:
            extra["marketplace_source"] = registered.source
        component_identity = f"plugin/{marketplace}/{name}"
    else:
        graph.warnings.append(
            f"plugin {name}@{marketplace} has no [marketplaces.{marketplace}] entry; "
            "identity is occurrence-local"
        )
        component_identity = f"plugin/{name}"

    self_ref = ComponentRef(
        name=name,
        version=_codex_plugin_version(version_dir, CODEX_SURFACE),
        component_identity=component_identity,
        source_manifest=str(version_dir),
        source_locator="$",
        extra=extra,
    )
    plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
    _add_child(graph, target, plugin_node)

    # Bundled content: the ordered manifest candidate list and folder discovery
    # are already `RepoSurface`-parameterised, so this reuses the repo-mode
    # plugin descent wholesale. Codex has no tier-2 install lockfile, so unlike
    # Claude Code the plugin's own root dep manifests are emitted here rather
    # than being suppressed in favour of a lockfile walk.
    descend(graph, plugin_node, version_dir, normalize, surface=CODEX_SURFACE)


def _record_codex_rules_coverage(graph: Graph, config_root: Path) -> None:
    """Surface `.rules` parse gaps during composition, not only at posture time.

    The approval DSL declares no components, so it produces no ref and would
    otherwise be invisible to coverage. Appending to the graph's warnings is
    enough for all three commands, because `scan endpoint`, `bom endpoint`, and
    `remote sync endpoint` already fold that list into `evidence_gaps`.
    """
    rules_dir = config_root / "rules"
    if not rules_dir.is_dir():
        return
    for path in sorted(rules_dir.glob("*.rules")):
        parsed = codex_rules.parse_rules(path)
        if parsed.unparsed_count:
            graph.warnings.append(f"{path}: {parsed.unparsed_count} unparsed rule(s)")


def build_codex_installed_graph(
    config_root: Path,
    project_root: Path | None = None,
    *,
    root_key: str = _TARGET_KEY,
    root_label: str = "codex",
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    """Endpoint-mode composition for Codex, owning the whole graph lifecycle.

    Deliberately does **not** call `build_rooted_graph` or `_seed_endpoint`.
    Both would run Claude Code's own plugin and remote-MCP acquisition against
    Codex's config root, producing Claude-shaped nodes on a Codex graph
    (ADR-0057). Only the two literal-substitution branches are shared, through
    `_seed_shared_endpoint_surfaces`.

    `finalize_graph` runs exactly once, after every seed, so MCP
    launch-dependency attachment sees Codex's own server refs and every seed's
    warnings reach the caller through the one copy it already does.
    """
    root = Node(key=root_key, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    normalize = _make_normalizer("endpoint", config_root, config_root, project_root, root_label)

    _seed_shared_endpoint_surfaces(
        graph,
        root,
        config_root,
        project_root,
        normalize,
        surface=CODEX_ENDPOINT,
        repo_surface=CODEX_SURFACE,
        by_scope=None,
    )
    _seed_codex_subagents(graph, root, config_root, normalize, project_root)
    _seed_codex_shared_agent_skills(graph, root, normalize)
    _seed_cache_plugins(graph, root, config_root, project_root, normalize, warnings=graph.warnings)
    _seed_codex_mcp_servers(
        graph, root, config_root, project_root, normalize, warnings=graph.warnings
    )
    _seed_codex_hooks(graph, root, config_root, project_root, normalize)
    _seed_codex_standalone_hooks(graph, root, config_root, project_root, normalize)
    _prune_codex_system_skills(graph, config_root)

    return finalize_graph(
        graph,
        config_root,
        normalize,
        project_root=project_root,
        include_gitignored=include_gitignored,
        attach_include_gitignored=True,
        root_dir=None,
        root_spec=None,
        warnings=warnings,
    )


def _prune_codex_system_skills(graph: Graph, config_root: Path) -> None:
    """Drop any skill sourced from a marker-bearing built-in root.

    The shared direct-skill walk already skips dot-prefixed subdirectories, so
    today's `.system` is excluded incidentally. This makes the exclusion rest on
    the marker instead, so a rename cannot silently re-admit vendor content.
    """
    roots = _codex_system_skill_roots(config_root)
    if not roots:
        return
    doomed = set()
    for key, node in graph.nodes.items():
        if node.ref is None or not node.ref.source_manifest:
            continue
        try:
            resolved = Path(node.ref.source_manifest).resolve()
        except OSError:
            continue
        if any(resolved.is_relative_to(root) for root in roots):
            doomed.add(key)
    for key in doomed:
        graph.nodes.pop(key, None)
    if doomed:
        graph.edges = [e for e in graph.edges if e.parent not in doomed and e.child not in doomed]
