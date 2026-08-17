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

import functools
import json
import tomllib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from pathspec import GitIgnoreSpec

from tools.component_ref import ComponentRef
from tools.endpoint_request import (
    claude_compat_agents_dir,
    endpoint_discovery_roots,
    endpoint_normalization_label,
)
from tools.graph import Edge, Graph, Node
from tools.hosts import HOSTS, all_host_ids
from tools.identity import canonical_component_identity, finalize_component_identity
from tools.mcp_launch_resolve import normalize_pypi_name, resolve_mcp_launch_dir
from tools.parsers import (
    ParserFn,
    agent_plugins,
    bun_lock,
    claude_command_agent,
    claude_install,
    claude_settings,
    claude_skill,
    hooks_json,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    registry_pattern_matches,
    resolve_host_selection,
    skill_lock,
    uv_lock,
)
from tools.parsers.claude_command_agent import Kind
from tools.parsers.claude_plugin_root import (
    _parse_bundled_command_agents,
    _parse_bundled_hooks,
    _parse_default_mcp,
    _parse_manifest_refs,
    default_mcp_filename_for_manifest,
    resolve_within,
)
from tools.parsers.gitignore import is_ignored, iter_unignored_files, load_gitignore_spec
from tools.parsers.settings_layers import SCOPE_PRECEDENCE
from tools.subagent_precedence import (
    group_occurrences_by_manifest,
    resolve_subagent_occurrences,
    resolve_subagent_occurrences_for_dirs,
)

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
    project_root: Path | None,
    *,
    discovery_roots: dict[str, Path] | None = None,
) -> SourceNormalizer:
    """Build the `source_manifest`-path normalizer for a scan.

    The node key's path portion must be a *stable logical path* (machine-specific
    root prefix stripped) so node keys — which become CycloneDX bom-refs — are
    reproducible across machines and dedup across them.

    - **repo mode**: the single scan `target` is the only root; the key path is
      `source_manifest` relative to `target` (POSIX), e.g.
      `.claude/skills/deploy/package.json`.
    - **endpoint mode**: paths span `project_root` (the project dir) and every
      endpoint discovery root — each selected host's config root plus the
      auxiliary roots endpoint composition reads (see
      `tools.endpoint_request.endpoint_discovery_roots`). Strip the matching
      known root and prefix its logical label so paths under different roots
      can't collide: `project/<rel>` under `project_root`, then `<label>/<rel>`
      per discovery root in descriptor order (`endpoint/` for Claude Code,
      `endpoint-<host_id>/` or `endpoint-<aux_label>/` for the rest). A path
      under neither falls back to the absolute path (last resort).
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
    project_r = project_root.resolve() if project_root is not None else None
    labeled_roots = [
        (label, root, root.resolve())
        for label, root in (discovery_roots or {"endpoint": target}).items()
    ]

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
        for label, root_logical, root_resolved in labeled_roots:
            rel = _rel(abs_path, root_logical, root_resolved)
            if rel is not None:
                return f"{label}/{rel}"
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
    hosts: list[str] | None = None,
    host_config_roots: dict[str, Path] | None = None,
    excluded_plugin_roots: list[Path] | None = None,
    realized_plugin_manifests: dict[Path, Path] | None = None,
) -> Graph:
    if mode not in ("repo", "endpoint"):
        raise ValueError(f"unknown mode: {mode!r}")
    hosts = hosts if hosts is not None else all_host_ids()
    if mode == "repo":
        # Same resolution `_active_registry` runs for parse_repo_grouped —
        # called here once, before descend()'s per-directory walk, so duplicate
        # host IDs are deduped and a colliding host selection fails loudly at
        # the single graph entry point, not per-directory or not at all. The
        # deduped list is what actually flows into descend() below, not the raw
        # `hosts` argument.
        hosts = resolve_host_selection(hosts)

    endpoint_roots: dict[str, Path] = {}
    discovery_roots: dict[str, Path] | None = None
    if mode == "endpoint":
        endpoint_roots = (
            {host_id: Path(r) for host_id, r in host_config_roots.items()}
            if host_config_roots
            else {"claude-code": Path(target)}
        )
        # Same unknown-ID rejection repo mode gets from `resolve_host_selection`:
        # without it, a typo'd host_config_roots key (e.g. "curser") reaches the
        # seeding loop below, silently `continue`s past the missing adapter, and
        # returns a graph containing only the target node — indistinguishable
        # from a legitimate "this host has nothing to report" result. The CLI
        # (`resolve_endpoint_request`) already rejects unknown host values
        # before it gets here, but a direct caller bypassing the CLI needs the
        # same guarantee build_graph itself gives repo mode.
        unknown = [host_id for host_id in endpoint_roots if host_id not in HOSTS]
        if unknown:
            known = ", ".join(sorted(HOSTS))
            raise ValueError(f"unknown host(s) {unknown!r}; known hosts: {known}")
        first_root = next(iter(endpoint_roots.values()))
        # `target` stays the API-compatibility anchor and the BOM target string.
        if Path(target) != first_root:
            raise ValueError(
                f"endpoint target {target} must equal the first host root {first_root}"
            )
        discovery_roots = endpoint_discovery_roots(list(endpoint_roots), endpoint_roots)

    root = Node(key=_TARGET_KEY, kind="target", ref=None)
    graph = Graph(nodes={root.key: root})
    # The node-key path normalizer (Stage 4): strips the machine-specific scan
    # root so node keys — which become CycloneDX bom-refs — are reproducible.
    # The gitignore root (`root_dir`/`root_spec`) and the normalize root derive
    # from the same scan root; they're separate concerns threaded in parallel.
    normalize = _make_normalizer(mode, Path(target), project_root, discovery_roots=discovery_roots)
    # ADR-0039 launch resolution context, set per-branch below.
    attach_root_dir: Path | None = None
    attach_root_spec: GitIgnoreSpec | None = None
    attach_include_gitignored = include_gitignored
    if mode == "endpoint":
        for host_id, host_root in endpoint_roots.items():
            adapter = HOSTS.get(host_id)
            if adapter is None or adapter.seed_endpoint is None:
                continue
            adapter.seed_endpoint(
                graph, root, host_root, project_root, normalize, warnings=warnings
            )
        _seed_endpoint_subagents(
            graph, root, endpoint_roots, project_root, normalize, hosts=list(endpoint_roots)
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
            hosts=hosts,
            excluded_plugin_roots=excluded_plugin_roots,
            realized_plugin_manifests=realized_plugin_manifests,
        )
        attach_root_dir = root_dir
        attach_root_spec = root_spec
    # Endpoint mode: project_root is separate from every host root, so its
    # manifests are absent from the host walks. Merge them in so a
    # project-scoped MCP declaring `npx <pkg>` can resolve by name against the
    # project's own package.json / pyproject.toml. project_root entries take
    # precedence. project_root is a user project dir — respect its .gitignore
    # (matching project-skill filtering); only host-root artifacts need the
    # unfiltered walk (attach_include_gitignored=True).
    project_name_index: dict[tuple[str, str], Path] = {}
    if project_root is not None:
        project_name_index = build_manifest_name_index(
            project_root, include_gitignored=include_gitignored
        )
    label_roots: dict[str, Path] = {}
    label_name_indexes: dict[str, dict[tuple[str, str], Path]] = {}
    if mode == "endpoint":
        # One index per host root, kept separate: a merged global map would let
        # one host's MCP bind a same-named package that exists only under
        # another host's root — a cross-host misattribution, not a fallback.
        for host_id, host_root in endpoint_roots.items():
            label = endpoint_normalization_label(host_id)
            label_roots[label] = host_root
            label_name_indexes[label] = {
                **build_manifest_name_index(
                    host_root, include_gitignored=attach_include_gitignored
                ),
                **project_name_index,
            }
        # The default index (used for project-scoped MCPs, which belong to no
        # host root) stays anchored to the PRIMARY host — `target`'s own host —
        # so today's project-over-install rule keeps exactly its single-host
        # meaning rather than gaining entries from a second host's root.
        name_index = next(iter(label_name_indexes.values()))
    else:
        name_index = {
            **build_manifest_name_index(Path(target), include_gitignored=attach_include_gitignored),
            **project_name_index,
        }
    _attach_mcp_launch_deps(
        graph,
        Path(target),
        normalize,
        name_index,
        project_root=project_root,
        include_gitignored=attach_include_gitignored,
        project_root_include_gitignored=include_gitignored,
        root_dir=attach_root_dir,
        root_spec=attach_root_spec,
        label_roots=label_roots,
        label_name_indexes=label_name_indexes,
    )
    graph.validate()
    return graph


def _seed_endpoint_subagents(
    graph: Graph,
    target: Node,
    roots: dict[str, Path],
    project_root: Path | None,
    normalize: SourceNormalizer,
    *,
    hosts: list[str],
) -> None:
    """Seed subagents once across every selected host, outside any single
    host's `seed_endpoint`.

    A shared-file occurrence (`<claude_root>/agents/helper.md` readable by both
    hosts) can span hosts, so no one host's seed can own it: per-host seeding
    would emit the same file twice, or dedup it into a selection-order-dependent
    host tag. The global scope passes each host's agents directory explicitly —
    an endpoint config root is an arbitrary path, so the dot-directory
    convention `resolve_subagent_occurrences` walks doesn't apply there. The
    project scope, where that convention genuinely holds, uses the repo-style
    resolver.
    """
    occurrences = list(
        resolve_subagent_occurrences_for_dirs(
            claude_compat_agents_dir(hosts, roots),
            (roots["cursor"] / "agents") if "cursor" in roots else None,
            hosts,
        )
    )
    if project_root is not None:
        # Honor the project root's .gitignore, parity with the project-scoped
        # skill seeds (both hosts' endpoint_seeds thread the same spec):
        # a subagent under an ignored path (.worktrees/, node_modules/) must
        # not be inventoried. Repo mode gets this via _is_ignored_under in
        # _add_repo_standalone_components; the endpoint project scope walks
        # here instead, so filter here.
        project_spec = load_gitignore_spec(project_root)
        occurrences.extend(
            occ
            for occ in resolve_subagent_occurrences(project_root, hosts)
            if not occ.source_manifest
            or not _is_ignored_under(Path(occ.source_manifest), project_root, project_spec)
        )
    for _manifest_path, refs in group_occurrences_by_manifest(occurrences):
        if not refs:
            continue
        self_node = Node(key=occurrence_key(refs[0], normalize), kind="agent", ref=refs[0])
        _add_child(graph, target, self_node)
        # Agents may declare frontmatter mcpServers/hooks; parse_file returns
        # them as subsequent refs. Attach them under the agent node (not the
        # target) so scope_of / lineage see the agent ancestor.
        for child_ref in refs[1:]:
            child_kind = _component_type(child_ref)
            if not isinstance(child_kind, str):
                continue
            child_node = Node(
                key=occurrence_key(child_ref, normalize), kind=child_kind, ref=child_ref
            )
            _add_child(graph, self_node, child_node)


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

    What is NOT seeded here:
    - Project skills under `<project_root>/.claude/skills/` (`_add_project_skills`,
      from `endpoint_seeds.claude_code.seed_endpoint`).
    - Remote MCPs from settings `mcpServers` and `.mcp.json` (`_seed_remote_mcps`).
    - Subagents at either scope (`_seed_endpoint_subagents`, cross-host).

    Seeding only the non-overlapping surfaces (rather than calling
    `_walk_direct_components` wholesale and relying on edge-dedup) keeps the two
    project-skill discovery paths from racing to own the node: their occurrence
    keys collide, so whichever ran first would silently win the ref content.
    """
    # Install-root direct skills: descend into each skill dir so its dep
    # manifests become package children of the skill node (parity with
    # `_add_skill_node` used for project skills and plugin-bundled skills).
    _add_direct_endpoint_skills(graph, target, install_root / "skills", normalize, project_root)

    # Personal commands: per-file parse so frontmatter-declared children attach
    # under the command node, not the target (parity with the `.md` branch of
    # `_add_repo_standalone_components`). Subagents are NOT seeded here — they
    # span hosts, so `_seed_endpoint_subagents` owns both scopes.
    _add_endpoint_command_agents(
        graph, target, install_root / "commands", normalize, kind="command"
    )

    # Project commands under `.claude/`.
    if project_root is not None:
        _add_endpoint_command_agents(
            graph, target, project_root / ".claude" / "commands", normalize, kind="command"
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
    hosts: list[str] | None = None,
    excluded_plugin_roots: list[Path] | None = None,
    realized_plugin_manifests: dict[Path, Path] | None = None,
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

    `hosts` gates which hosts' registry entries the `target` branch dispatches
    on. The `plugin`/`skill` branches never read it — they are Claude-only in
    this phase — so their recursive calls fall back to its default.

    `excluded_plugin_roots`, when given, is extended with every native plugin
    bundle root discovered but not realized — either because its owning host
    isn't selected, or a selected-host candidate manifest failed to realize
    while a sibling unselected-host candidate manifest also exists in the
    same bundle root (the `target` branch's own `unselected_host_plugin_roots`)
    — the graph excludes their subtree from discovery internally, but callers
    doing an independent filesystem walk of the same directory (posture
    manifest collection in `tools/scan.py`) need the same boundary to avoid
    misattributing an unselected host's bundled manifest via `owning_host`'s
    path-shape fallback.

    `realized_plugin_manifests`, when given, is populated with
    `{plugin_root: winning_manifest_path}` for every native plugin root that
    DID realize — the manifest candidate `_descend_into_plugin` actually
    parsed, as opposed to a sibling native-format manifest in the same
    directory that lost the realization race (see `_find_plugin_roots`).
    Callers doing an independent filesystem walk that globs both native
    manifest names in one pass (posture manifest collection in
    `tools/scan.py`) need this to avoid treating the losing sibling's content
    as belonging to the realized plugin.
    """
    hosts = hosts if hosts is not None else all_host_ids()
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
        # A native plugin root whose owning host isn't selected is never
        # realized as a plugin node, but its bundle boundary is still real —
        # without this, an unselected-host bundle's bare `mcp.json` (e.g. a
        # Cursor plugin's bundled `<root>/mcp.json`, which collides with
        # Claude's own bare `mcp.json` pattern) falls through to the
        # standalone-surface walk below and gets misattributed to the target
        # under the wrong host.
        unselected_host_plugin_roots: list[Path] = []
        for plugin_root, manifest_candidates in plugin_roots:
            plugin_node = None
            winning_manifest: Path | None = None
            any_unselected_host_candidate = False
            for plugin_manifest in manifest_candidates:
                parser = _plugin_parser_for_path(plugin_manifest, directory, hosts)
                if parser is None:
                    # This candidate's owning host isn't selected — content
                    # validity is never checked for it (no parser is invoked),
                    # so its mere presence is proof the directory is a bundle
                    # for that other host, regardless of whether a sibling
                    # selected-host candidate also fails below.
                    any_unselected_host_candidate = True
                    continue
                plugin_node = _descend_into_plugin(
                    graph,
                    parent,
                    plugin_root,
                    plugin_manifest,
                    normalize,
                    parser=parser,
                    root_dir=root_dir,
                    root_spec=root_spec,
                )
                # A candidate that realizes wins outright — a directory with
                # both a broken preferred manifest and a valid fallback one
                # must still produce a plugin node, not silently drop to the
                # standalone/subagent walks below (see `_find_plugin_roots`).
                if plugin_node is not None:
                    winning_manifest = plugin_manifest
                    break
            if plugin_node is not None:
                realized_roots.append(plugin_root)
                if realized_plugin_manifests is not None and winning_manifest is not None:
                    realized_plugin_manifests[plugin_root] = winning_manifest
            elif any_unselected_host_candidate:
                # Covers both "every candidate belongs to an unselected host"
                # and "the selected-host candidate is malformed but a sibling
                # unselected-host candidate also exists" — either way there is
                # a real plugin bundle here we can't/didn't realize, so its
                # subtree must still be excluded from the standalone/subagent
                # walks below, not just directories with zero selected
                # candidates at all.
                unselected_host_plugin_roots.append(plugin_root)
        if excluded_plugin_roots is not None:
            excluded_plugin_roots.extend(unselected_host_plugin_roots)
        # Agent Plugins bundles (content-detected, outside manifest_registry)
        # realize here too, BEFORE the project-skill and standalone-surface
        # walks below, so a bundle's whole subtree — a host-private
        # `.cursor/commands/`, `.claude/agents/`, or `.cursor/skills/` path
        # nested inside it, not just its root `mcp.json` — is excluded from
        # them via `exclude_under`, the same single-parent mechanism native
        # plugin roots already get. Gated on "cursor" like every other Agent
        # Plugins surface (ADR-0045).
        #
        # Collected into a SEPARATE list from `realized_roots`: the closed
        # Agent Plugins contract never reads the bundle root's own dependency
        # manifests (`_realize_agent_plugin`'s docstring), so a target-level
        # `package.json` beside a root Agent Plugins bundle must keep its
        # target-level dep nodes — unlike a native plugin root, an Agent
        # Plugins root must NOT gate `_add_dep_manifest_packages` below.
        standalone_exclude_roots = list(realized_roots) + unselected_host_plugin_roots
        if "cursor" in hosts:
            # exclude_under gets the FULL boundary list (realized + unselected-
            # host bundle roots), not just realized_roots: an unselected-host
            # bundle's example/fixture plugin.json must not realize as an
            # independent Agent Plugins bundle only because the owning host
            # wasn't selected — inventory must not depend on host selection
            # that way.
            for manifest_path in _find_agent_plugin_roots(
                directory,
                exclude_under=standalone_exclude_roots,
                include_gitignored=include_gitignored,
            ):
                plugin_node = _realize_agent_plugin(
                    graph,
                    parent,
                    manifest_path,
                    normalize,
                    root_dir=root_dir,
                    root_spec=root_spec,
                )
                if plugin_node is not None:
                    standalone_exclude_roots.append(manifest_path.parent)
        _add_project_skills(
            graph,
            parent,
            directory,
            normalize=normalize,
            exclude_under=standalone_exclude_roots,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
            hosts=hosts,
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
            exclude_under=standalone_exclude_roots,
            include_gitignored=include_gitignored,
            root_dir=root_dir,
            root_spec=root_spec,
            hosts=hosts,
        )
    elif parent.kind == "plugin":
        plugin_manifest_path, plugin_runtime_hosts = _plugin_manifest_context(parent, directory)
        _add_bundled_skills(
            graph,
            parent,
            directory,
            normalize,
            plugin_manifest_path=plugin_manifest_path,
            runtime_hosts=plugin_runtime_hosts,
            root_dir=root_dir,
            root_spec=root_spec,
        )
        _add_bundled_plugin_surfaces(
            graph,
            parent,
            directory,
            normalize,
            plugin_manifest_path=plugin_manifest_path,
            runtime_hosts=plugin_runtime_hosts,
            root_dir=root_dir,
            root_spec=root_spec,
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


_PLUGIN_REGISTRY_PATTERNS = frozenset({".claude-plugin/plugin.json", ".cursor-plugin/plugin.json"})

# Directory names that own their own `plugin.json` via a directory-scoped
# registry pattern above. A bare `plugin.json` nested under one of these is
# that native format's manifest, never an Agent Plugins root — the content-
# based dispatch in `_add_repo_standalone_components` skips it regardless of
# whether the native plugin realization actually succeeded for that dir.
_PLUGIN_MANIFEST_CONFIG_DIRS = frozenset({".claude-plugin", ".cursor-plugin"})


def _plugin_parser_for_path(path: Path, root: Path, hosts: list[str]) -> ParserFn | None:
    """First selected host's manifest_registry entry whose pattern is
    plugin-shaped and matches `path`, or None.

    Same allowlist idiom as `_mcp_parser_for_path`/`_skill_parser_for_path`/
    `_command_parser_for_path`: used only to *choose* which parser owns a
    matched manifest (Cursor's registry entry is pre-bound to
    `runtime_hosts=["cursor"]`); placement stays the plugin branch's own.
    """
    for host_id in hosts:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, parser in adapter.manifest_registry:
            if pattern in _PLUGIN_REGISTRY_PATTERNS and registry_pattern_matches(
                path, root, pattern
            ):
                return parser
    return None


def _find_plugin_roots(
    directory: Path, *, include_gitignored: bool = False
) -> list[tuple[Path, list[Path]]]:
    """Plugin roots are dirs containing a `.claude-plugin/plugin.json` or
    `.cursor-plugin/plugin.json`, at ANY depth (parity with parse_repo).
    Discovery is host-agnostic — the caller decides, per root, whether the
    owning host is selected (realize a plugin node) or not (still a bundle
    boundary, excluded from standalone/subagent discovery but no node
    emitted) via `_plugin_parser_for_path`. Discovery uses the same
    gitignore-aware walk as project-skill discovery so we skip
    `node_modules/`, `.git/`, gitignored dirs. Returns `(plugin_root,
    [manifest_path, ...])` pairs sorted by plugin_root for determinism.

    One directory carrying BOTH native manifests yields ONE root with BOTH
    manifest paths as candidates, in walk order: `iter_unignored_files` sorts
    directory entries, so `.claude-plugin/plugin.json` always precedes
    `.cursor-plugin/plugin.json` regardless of host-selection order. The
    caller (`descend()`'s target branch) tries candidates in that order and
    falls back to the next one if a preferred candidate's host isn't
    selected, or its manifest fails to realize (malformed JSON, no self-ref)
    — so a valid Cursor-format manifest still realizes the plugin even when a
    sibling Claude-format manifest in the same directory is broken, instead
    of the whole root silently going unrealized. When every candidate in a
    directory is itself valid, the first (Claude-format) one still wins, same
    as before — pinned by
    `test_repo_dual_native_plugin_manifests_resolve_to_claude_format`. Repo-
    mode manifest accounting still counts both files independently; only the
    graph's realization choice is affected here.
    """
    spec = None if include_gitignored else load_gitignore_spec(directory)
    roots: dict[Path, list[Path]] = {}
    order: list[Path] = []
    for path in iter_unignored_files(directory, spec):
        if path.name != "plugin.json" or path.parent.name not in (
            ".claude-plugin",
            ".cursor-plugin",
        ):
            continue
        root = path.parent.parent
        # A manifest that is a symlink escaping its own bundle root must not
        # be a candidate at all: realizing it mints plugin self-identity from
        # a document outside the bundle, and the same external content is
        # later re-read by _plugin_manifest_data/_plugin_custom_skills_field.
        # (os.walk prunes symlinked dirs, but symlinked FILES still appear.)
        if resolve_within(root, f"{path.parent.name}/plugin.json") is None:
            continue
        resolved = root.resolve()
        if resolved not in roots:
            roots[resolved] = []
            order.append(root)
        roots[resolved].append(path)
    pairs = [(root, roots[root.resolve()]) for root in order]
    pairs.sort(key=lambda pair: pair[0])
    return pairs


def _plugin_manifest_context(plugin_node: Node, plugin_root: Path) -> tuple[Path, list[str] | None]:
    """Derive `(plugin_manifest_path, runtime_hosts)` from the plugin node's
    own self ref, rather than re-deriving from a hardcoded location.

    Repo mode's self ref (`claude_plugin.parse`, either format) sets
    `source_manifest` to the real manifest path that was matched, so this
    reads back correctly for both `.claude-plugin/plugin.json` and
    `.cursor-plugin/plugin.json`. Endpoint mode's `_seed_active_plugins`
    instead sources its self ref from `installed_plugins.json` (for
    lockfile-accurate version/gitCommitSha) — not a real plugin manifest —
    so that case falls back to the historical `.claude-plugin/plugin.json`
    default, preserving endpoint mode's existing (Claude-only) behavior.
    """
    plugin_ref = plugin_node.ref
    runtime_hosts = plugin_ref.extra.get("runtime_hosts") if plugin_ref is not None else None
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
    if plugin_ref is not None and plugin_ref.source_manifest:
        candidate = Path(plugin_ref.source_manifest)
        if candidate.name == "plugin.json" and candidate.parent.name in (
            ".claude-plugin",
            ".cursor-plugin",
        ):
            manifest_path = candidate
    return manifest_path, runtime_hosts


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
    label_roots: dict[str, Path] | None = None,
    label_name_indexes: dict[str, dict[tuple[str, str], Path]] | None = None,
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

    In endpoint mode `label_roots`/`label_name_indexes` carry one entry per host
    root, keyed by the node key's normalization label prefix — so each MCP node
    resolves against the root that seeded it and that root's own name index.
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
        # Endpoint mode spans one root per selected host plus a separate
        # project_root; a local launch path declared in a project manifest
        # resolves under project_root, so use it as the scan_root when this MCP
        # was declared there.
        effective_scan_root = scan_root
        effective_name_index = name_index
        from_project = False
        if project_root is not None and mcp.ref.source_manifest:
            try:
                if Path(mcp.ref.source_manifest).resolve().is_relative_to(project_root.resolve()):
                    effective_scan_root = project_root
                    from_project = True
            except (ValueError, OSError):
                pass
        if label_roots and not from_project:
            # Otherwise resolve against the host root that seeded this node,
            # recovered from its key's normalization label. A label with no host
            # root behind it is an auxiliary or unmapped root: those contribute
            # no name index and no launch resolution at all (contract item 3) —
            # falling back to the primary host's root/index would bind the node
            # to a root that does not own it.
            label = mcp.key.split("/", 1)[0]
            owning_root = label_roots.get(label)
            if owning_root is None:
                continue
            effective_scan_root = owning_root
            effective_name_index = (label_name_indexes or {}).get(label, name_index)
        resolved = resolve_mcp_launch_dir(
            mcp.ref, scan_root=effective_scan_root, name_index=effective_name_index
        )
        if resolved is None:
            continue
        # Project-scoped MCPs (effective_scan_root is project_root) use the
        # project-root gitignore context, matching project-skills and project
        # name-index filtering. Install-root MCPs use the endpoint-wide context
        # (include_gitignored=True; installed artifacts are never filtered).
        if from_project:
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
    parser: ParserFn,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> Node | None:
    """Create the plugin node (child of target) and descend into its subtree.

    Reuses the registry-selected parser (`claude_plugin.parse`, or Cursor's
    `runtime_hosts=["cursor"]`-bound partial) only to obtain the plugin
    self-identity ref; placement (the plugin → target edge, and which
    children hang off the plugin) is owned here.

    Returns the created plugin node, or `None` when the manifest is malformed
    or yields no self-ref. A `None` return means the directory is NOT an owned
    plugin subtree, so the caller must not exclude it from sibling discovery.
    """
    parsed = _safe_parse(parser, plugin_manifest)
    self_ref = next((r for r in parsed if _component_type(r) == "plugin"), None)
    if self_ref is None:
        return None
    plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
    _add_child(graph, target, plugin_node)
    descend(graph, plugin_node, plugin_root, normalize, root_dir=root_dir, root_spec=root_spec)
    return plugin_node


def _realize_agent_plugin(
    graph: Graph,
    parent: Node,
    manifest_path: Path,
    normalize: SourceNormalizer,
    *,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
    self_ref_extra: dict[str, object] | None = None,
) -> Node | None:
    """Closed, parser-output-only realization for an Agent Plugins bundle
    (ADR-0045 Decision #3): every node attached here comes straight from
    `agent_plugins.parse`'s own ref list — self, skills, MCP servers — never
    a fresh read of the bundle. Unlike Task 13's native plugin descent
    (`_descend_into_plugin`), nothing else in the bundle is enumerated at
    realization time: no hooks/commands/agents, no plugin-root dependency-
    manifest walk, no `extensions` read. That richer surface set is exactly
    what the portable v1 contract excludes.

    `root_dir`/`root_spec` thread the scan-root `.gitignore` context into the
    bundled-skill descent, matching Task 13's `_descend_into_plugin` — a
    gitignored dep manifest under an Agent Plugin's bundled skill must stay
    excluded in repo mode. Callers that omit them (Task 17's endpoint seed)
    keep the historical unfiltered behavior.

    Returns the created plugin node, or `None` when the parse yields no refs
    or the first ref isn't the plugin self ref (e.g. a schema-tagged manifest
    missing `name` — a malformed-but-detected bundle attaches nothing).
    Callers use this `None` to know that nothing was actually realized, so
    they must not treat any of the bundle's other files (e.g. its own root
    `mcp.json`) as claimed.

    Reused verbatim by Task 17's endpoint seed for dev-linked Agent Plugins,
    so repo and endpoint mode cannot drift into two interpretations of the
    closed surface. `self_ref_extra`, when given, is merged onto the plugin
    self ref's `extra` only (ADR-0045 Decision #7's cached-plugin caller uses it to stamp
    `cursor_marketplace_dir`; dev-linked callers omit it).
    """
    refs = _safe_parse(
        functools.partial(agent_plugins.parse, runtime_hosts=["cursor"]), manifest_path
    )
    if not (refs and _component_type(refs[0]) == "plugin"):
        return None
    self_ref = refs[0]
    if self_ref_extra:
        self_ref = replace(self_ref, extra={**self_ref.extra, **self_ref_extra})
    plugin_node = Node(key=occurrence_key(self_ref, normalize), kind="plugin", ref=self_ref)
    plugin_node = _add_child(graph, parent, plugin_node)
    for ref in refs[1:]:
        component_type = _component_type(ref)
        if not isinstance(component_type, str):
            continue
        child_node = Node(key=occurrence_key(ref, normalize), kind=component_type, ref=ref)
        child_node = _add_child(graph, plugin_node, child_node)
        if component_type == "skill" and ref.source_manifest:
            descend(
                graph,
                child_node,
                Path(ref.source_manifest).parent,
                normalize,
                root_dir=root_dir,
                root_spec=root_spec,
            )
    return plugin_node


_SKILL_REGISTRY_PATTERNS = frozenset(
    {
        "**/.claude/skills/*/SKILL.md",
        "**/.cursor/skills/*/SKILL.md",
        "**/.agents/skills/*/SKILL.md",
    }
)

# The host-owned config directory names those skill patterns are anchored on
# (`.claude`, `.cursor`, `.agents`), derived from the patterns themselves so
# the two can't drift. A bare `plugin.json` sitting directly in one of these
# is host configuration, never an Agent Plugins bundle root: the bundle root's
# `skills/<name>/SKILL.md` would then BE `<config-dir>/skills/<name>/SKILL.md`,
# an occurrence `_add_project_skills` discovers independently and parents to
# the target — realizing the bundle too would put one occurrence under two
# parents and abort the scan.
_SKILL_ROOT_CONFIG_DIRS = frozenset(pattern.split("/")[1] for pattern in _SKILL_REGISTRY_PATTERNS)


def _find_agent_plugin_roots(
    directory: Path,
    *,
    exclude_under: list[Path] | None = None,
    include_gitignored: bool = False,
) -> list[Path]:
    """Agent Plugins bundle roots: dirs with a bare `plugin.json` (ANY depth)
    that content-detects as an Agent Plugins manifest (ADR-0045 Decision #3).
    Unlike `_find_plugin_roots` there's no registry pattern to dispatch on —
    detection is schema-content-based, so a native `.claude-plugin/plugin.json`
    or `.cursor-plugin/plugin.json` (`_PLUGIN_MANIFEST_CONFIG_DIRS`) and a bare
    plugin.json sitting directly in a host skill-config dir
    (`_SKILL_ROOT_CONFIG_DIRS`) are never candidates.

    `exclude_under` (native plugin roots already realized by the caller) keeps
    a native plugin's own bundled example/fixture content from being picked up
    as a second, independent Agent Plugins bundle. Returns manifest paths
    sorted for determinism.
    """
    spec = None if include_gitignored else load_gitignore_spec(directory)
    exclude_resolved = [p.resolve() for p in exclude_under] if exclude_under else []
    manifests: list[Path] = []
    for path in iter_unignored_files(directory, spec):
        if path.name != "plugin.json" or path.parent.name in _PLUGIN_MANIFEST_CONFIG_DIRS:
            continue
        if path.parent.name in _SKILL_ROOT_CONFIG_DIRS:
            continue
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
            continue
        # Same containment rule as _find_plugin_roots: the bundle root is the
        # manifest's parent, so a symlinked plugin.json escaping it must not
        # be schema-detected (widest-reach read — this runs against every
        # bare plugin.json in the tree).
        if resolve_within(path.parent, "plugin.json") is None:
            continue
        if not agent_plugins.is_agent_plugins_manifest(path):
            continue
        manifests.append(path)
    manifests.sort()
    return manifests


def _skill_parser_for_path(path: Path, root: Path, hosts: list[str]) -> ParserFn | None:
    """First selected host's manifest_registry entry whose pattern is
    skill-shaped and matches `path`, or None.

    Same allowlist trade-off as `_mcp_parser_for_path`: a new host reusing one
    of these three directory-name shapes still needs one line added to this
    set for its own distinct pattern string — allowlisting is per pattern
    string, not per shape. Reusing an existing pattern string verbatim needs
    no allowlist change, but only holds when that string's existing owner
    host isn't also selected in the same scan; `resolve_host_selection`
    rejects two distinct, simultaneously selected hosts claiming the same
    pattern string (see ADR-0044, Decision #1).
    """
    for host_id in hosts:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, parser in adapter.manifest_registry:
            if pattern in _SKILL_REGISTRY_PATTERNS and registry_pattern_matches(
                path, root, pattern
            ):
                return parser
    return None


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
    hosts: list[str] | None = None,
) -> None:
    """Project skills live at `.claude/skills/<name>/SKILL.md` at ANY depth.

    Discovery uses the same gitignore-aware tree walk as `parse_repo_grouped`
    so we skip `node_modules/`, `.git/`, and gitignored dirs. Each skill dir
    becomes a `skill` child of `parent` (the target). Symlinked directories are
    not followed (matches the current scanner; tracked separately).

    `exclude_under` is the set of plugin roots already descended from `parent`:
    skills inside any of those subtrees belong to the plugin branch
    (single-parent invariant), so they are skipped here to avoid double-discovery.

    Which skill directory shapes count, and how their refs are host-tagged,
    comes from each selected host's `HostAdapter.manifest_registry` via the
    same `registry_pattern_matches` `parse_repo_grouped` uses — so graph
    placement and manifest accounting can never independently decide a path
    belongs to different hosts.
    """
    hosts = hosts if hosts is not None else all_host_ids()
    eval_root, spec = _ignore_context(directory, include_gitignored, root_dir, root_spec)
    # The walk yields paths relative to `directory`; ignore checks evaluate
    # relative to `eval_root` (the scan root in repo mode). When the walk root and
    # eval root differ, evaluate the absolute path against eval_root.
    walk_spec = spec if eval_root == directory else None
    exclude_resolved = [p.resolve() for p in exclude_under] if exclude_under else []
    for path in iter_unignored_files(directory, walk_spec):
        if path.name != "SKILL.md":
            continue
        skill_parser = _skill_parser_for_path(path, directory, hosts)
        if skill_parser is None:
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
            skill_parser=skill_parser,
        )


def _add_bundled_skills(
    graph: Graph,
    parent: Node,
    directory: Path,
    normalize: SourceNormalizer,
    *,
    plugin_manifest_path: Path,
    runtime_hosts: list[str] | None = None,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    """Plugin-bundled skills live at `<plugin-root>/skills/<name>/SKILL.md`,
    or at a custom directory named by the manifest's `"skills"` field.

    Path resolution mirrors `claude_plugin_root._parse_bundled_skills`:
    `resolve_within` rejects traversal outside the plugin root, the default
    `skills/` is always tried, and a custom dir equal to the default is
    deduped. `plugin_manifest_path` is the plugin's own manifest (either
    format), resolved once by the caller from the plugin node's self ref —
    never re-derived from a hardcoded `.claude-plugin` location.
    """
    skill_dirs: list[Path] = []
    default_skills = resolve_within(directory, "skills")
    if default_skills is not None and default_skills.is_dir():
        skill_dirs.append(default_skills)
    custom_skills = _plugin_custom_skills_field(plugin_manifest_path, plugin_root=directory)
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
            runtime_hosts=runtime_hosts,
            root_dir=root_dir,
            root_spec=root_spec,
        )


def _plugin_custom_skills_field(plugin_manifest_path: Path, *, plugin_root: Path) -> object:
    if not _manifest_within_root(plugin_manifest_path, plugin_root):
        return None
    try:
        data = json.loads(plugin_manifest_path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("skills")


def _manifest_within_root(manifest: Path, root: Path) -> bool:
    """Whether `manifest`, after following symlinks, still lives inside the
    resolved `root`. Guards the bundled-surface content reads: a plugin
    manifest that is a symlink escaping its bundle must not drive custom
    paths or inline declarations (repo-mode candidates are pre-filtered by
    `_find_plugin_roots`; endpoint mode's hardcoded default path is not)."""
    try:
        return manifest.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


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
    runtime_hosts: list[str] | None = None,
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
            runtime_hosts=runtime_hosts,
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
    runtime_hosts: list[str] | None = None,
    skill_parser: ParserFn | None = None,
) -> None:
    """Create a skill node (child of `parent`) and descend into its dep manifests.

    `stamp_provenance` is set ONLY by the endpoint project-skill walk
    (`_add_project_skills` invoked from `endpoint_seeds.claude_code.seed_endpoint`),
    matching the old
    `_walk_project_skill_dirs` → `_parse_direct_skill` path that stamped
    `extra["source_provenance"]` from a `skills-lock.json` / symlink target.
    Repo-mode `.claude/skills` (old REGISTRY `claude_skill.parse`, no stamp) and
    plugin-bundled skills (old `walk_plugin_root`, no stamp) leave it False.

    `skill_parser` is the registry-provided parser, passed only by the
    registry-driven caller (`_add_project_skills`); the Claude-only callers
    pass neither it nor `runtime_hosts` and keep the `["claude-code"]` default.
    """
    if skill_parser is None:
        runtime_hosts = runtime_hosts if runtime_hosts is not None else ["claude-code"]
        skill_parser = functools.partial(claude_skill.parse, runtime_hosts=runtime_hosts)
    skill_md = skill_subdir / "SKILL.md"
    for ref in _safe_parse(skill_parser, skill_md):
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

_MCP_REGISTRY_PATTERNS = frozenset({*_STANDALONE_MCP_FILENAMES, ".cursor/mcp.json"})


_COMMAND_REGISTRY_PATTERNS = frozenset(
    {"**/.claude/commands/**/*.md", "**/.cursor/commands/**/*.md"}
)


def _command_parser_for_path(path: Path, root: Path, hosts: list[str]) -> ParserFn | None:
    """First selected host's manifest_registry entry whose pattern is
    command-shaped and matches `path`, or None."""
    for host_id in hosts:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, parser in adapter.manifest_registry:
            if pattern not in _COMMAND_REGISTRY_PATTERNS:
                continue
            if registry_pattern_matches(path, root, pattern):
                return parser
    return None


def _mcp_parser_for_path(path: Path, root: Path, hosts: list[str]) -> ParserFn | None:
    """First selected host's manifest_registry entry whose pattern is
    MCP-shaped and matches `path`, or None.

    The `_MCP_REGISTRY_PATTERNS` allowlist is this function's one
    remaining piece of host-specific knowledge: manifest_registry's
    `(pattern, ParserFn)` shape doesn't itself say what kind of
    component a pattern produces, only which parser to call for it.
    Allowlisting is per pattern *string*, not per filename/location
    *shape*: a new host reusing one of these shapes with its own
    distinct pattern string still needs one line added to this set —
    smaller and more centralized than the old "write a new hardcoded
    dispatch branch" ask, but not zero. Reusing an existing pattern
    string verbatim needs no allowlist change, but only holds when that
    string's existing owner host isn't also selected in the same scan;
    `resolve_host_selection` rejects two distinct, simultaneously
    selected hosts claiming the same pattern string (see ADR-0044,
    Decision #1).
    """
    for host_id in hosts:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, parser in adapter.manifest_registry:
            if pattern in _MCP_REGISTRY_PATTERNS and registry_pattern_matches(path, root, pattern):
                return parser
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
    hosts: list[str] | None = None,
) -> None:
    """Repo target-level standalone surfaces: MCP manifests and `.claude`
    commands/agents discovered at any depth (parity with the parser REGISTRY),
    each a child of the target.

    Files inside a plugin subtree are skipped (`exclude_under` = the native
    and Agent Plugins bundle roots already descended from the target, per
    `descend`) so a plugin's bundled MCP/command surfaces stay under the
    plugin node (single-parent).

    The MCP surface resolves its parser through each selected host's
    `HostAdapter.manifest_registry`, using the same `registry_pattern_matches`
    `parse_repo_grouped` uses — so graph placement and manifest accounting
    can never independently decide a path belongs to different hosts.
    """
    hosts = hosts if hosts is not None else all_host_ids()
    eval_root, spec = _ignore_context(directory, include_gitignored, root_dir, root_spec)
    walk_spec = spec if eval_root == directory else None
    exclude_resolved = [p.resolve() for p in exclude_under] if exclude_under else []

    def _skip(path: Path) -> bool:
        if _is_ignored_under(path, eval_root, spec):
            return True
        resolved = path.resolve()
        return any(resolved.is_relative_to(root) for root in exclude_resolved)

    paths = [path for path in iter_unignored_files(directory, walk_spec) if not _skip(path)]

    for path in paths:
        if path.name == "plugin.json" and path.parent.name not in _PLUGIN_MANIFEST_CONFIG_DIRS:
            # Agent Plugins bundle roots realize in the caller (`descend`,
            # target branch) before this function runs at all, and their
            # whole subtree — including this manifest and its sibling
            # `mcp.json` — is already excluded via `exclude_under`. A bare
            # plugin.json that reaches this point either belongs to a bundle
            # that failed to realize (schema/name check) or isn't an Agent
            # Plugins manifest at all; either way it is never a command/
            # agent/mcp/settings surface itself.
            continue

        mcp_parser = _mcp_parser_for_path(path, directory, hosts)
        if mcp_parser is not None:
            for ref in _safe_parse(mcp_parser, path):
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
            command_parser = _command_parser_for_path(path, directory, hosts)
            if command_parser is None:
                continue
            refs = _safe_parse(command_parser, path)
            if not refs:
                continue
            self_node = Node(key=occurrence_key(refs[0], normalize), kind="command", ref=refs[0])
            _add_child(graph, parent, self_node)
            for child_ref in refs[1:]:
                child_kind = _component_type(child_ref)
                if not isinstance(child_kind, str):
                    continue
                child_node = Node(
                    key=occurrence_key(child_ref, normalize), kind=child_kind, ref=child_ref
                )
                _add_child(graph, self_node, child_node)

    # Subagents (`.claude/agents/**/*.md`, `.cursor/agents/**/*.md`) are
    # resolved as one directory-wide precedence pass rather than per-path
    # like the surfaces above: pairing a Cursor override with its Claude
    # counterpart needs to see both files before deciding either file's
    # occurrence count (Task 12), which the single-path loop above can't do.
    for manifest_path, refs in group_occurrences_by_manifest(
        resolve_subagent_occurrences(directory, hosts)
    ):
        if _is_ignored_under(manifest_path, eval_root, spec):
            continue
        resolved = manifest_path.resolve()
        if any(resolved.is_relative_to(root) for root in exclude_resolved):
            continue
        if not refs:
            continue
        self_node = Node(key=occurrence_key(refs[0], normalize), kind="agent", ref=refs[0])
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


def _add_bundled_plugin_surfaces(
    graph: Graph,
    plugin_node: Node,
    plugin_root: Path,
    normalize: SourceNormalizer,
    *,
    plugin_manifest_path: Path,
    runtime_hosts: list[str] | None = None,
    root_dir: Path | None = None,
    root_spec: GitIgnoreSpec | None = None,
) -> None:
    """A plugin's bundled non-skill surfaces (MCPs, hooks, commands, agents) →
    children of the plugin node. Reuses the shared `claude_plugin_root` helpers
    for content; placement is owned here (parent-by-construction).

    Bundled skills are NOT added here — the `_add_bundled_skills` descent already
    creates them and their dep chains; re-emitting via the surface walker would
    double-create. Parentage of every bundled surface is set by the graph edge
    from the plugin node below, not stored on the refs. `plugin_manifest_path`
    is the plugin's own manifest (either format), resolved once by the caller
    from the plugin node's self ref — never re-derived from a hardcoded
    `.claude-plugin` location, so a Cursor bundle's `mcp.json` default and
    inline hooks are read from the manifest that was actually matched.
    """
    plugin_ref = plugin_node.ref
    if plugin_ref is None:
        return
    plugin_name = plugin_ref.name or ""
    plugin_data = _plugin_manifest_data(plugin_manifest_path, plugin_root=plugin_root)
    default_mcp_filename = default_mcp_filename_for_manifest(plugin_manifest_path)

    refs: list[ComponentRef] = []
    manifest_refs = _parse_manifest_refs(
        plugin_data,
        plugin_json_path=plugin_manifest_path,
        plugin_root=plugin_root,
        runtime_hosts=runtime_hosts,
    )
    refs.extend(manifest_refs)
    refs.extend(
        _parse_default_mcp(
            plugin_root,
            manifest_refs,
            default_filename=default_mcp_filename,
            runtime_hosts=runtime_hosts,
        )
    )
    refs.extend(
        _parse_bundled_hooks(
            plugin_root,
            plugin_data,
            plugin_name,
            plugin_json_path=plugin_manifest_path,
            runtime_hosts=runtime_hosts,
        )
    )
    refs.extend(
        _parse_bundled_command_agents(
            plugin_root, plugin_data, plugin_name, runtime_hosts=runtime_hosts
        )
    )
    refs = [r for r in refs if _component_type(r) != "skill"]
    # Stamp plugin-container context (declared_by.kind=plugin + a
    # plugin-prefixed component_path) onto each bundled ref. This is placement
    # metadata the descent owns — parity with the pre-graph `_with_plugin_context`
    # that the endpoint walker applied — not a content read.
    refs = claude_install._with_plugin_context(
        refs, plugin_name, plugin_manifest_path, runtime_hosts=runtime_hosts
    )
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


def _plugin_manifest_data(plugin_manifest_path: Path, *, plugin_root: Path) -> dict:
    if not _manifest_within_root(plugin_manifest_path, plugin_root):
        return {}
    try:
        data = json.loads(plugin_manifest_path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
