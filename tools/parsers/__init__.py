"""Manifest parser registry."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Callable

from tools.component_ref import ComponentRef
from tools.host_paths import owning_host
from tools.parsers import (
    agent_plugins,
    bun_lock,
    claude_command_agent,
    claude_plugin,
    claude_settings,
    claude_skill,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    uv_lock,
)
from tools.parsers.claude_plugin_root import resolve_within
from tools.parsers.gitignore import is_ignored, iter_unignored_files, load_gitignore_spec

ParserFn = Callable[[Path], list[ComponentRef]]


def _parse_repo_command(path: Path) -> list[ComponentRef]:
    return claude_command_agent.parse_file(path, kind="command")


# Software-dependency / lockfile manifests: no host concept, always active
# regardless of which hosts are selected.
#
# Plan 009 lockfile parsers give repo-mode transitive coverage:
# extra["transitive"]=True so SARIF surfaces properties.coverage=transitive.
HOST_AGNOSTIC_REGISTRY: list[tuple[str, ParserFn]] = [
    ("package.json", package_json.parse),
    ("pyproject.toml", pyproject_toml.parse),
    ("package-lock.json", package_lock_json.parse),
    ("uv.lock", uv_lock.parse),
    ("bun.lock", bun_lock.parse),
]

# Claude Code's agent-component surfaces (ADR-0044). Unchanged content from
# the pre-split REGISTRY; only the name and grouping changed.
#
# `claude_desktop_config.json` is Claude Desktop user-config: same JSON shape
# as `mcp.json` (`mcpServers` map of stdio launches), different filename —
# same parser, the filename pattern is the only addition. The skill/command/
# agent patterns (plan 008) emit the same ecosystems as endpoint mode;
# parentage is set by the graph edge, not stored on the refs.
CLAUDE_CODE_MANIFEST_REGISTRY: list[tuple[str, ParserFn]] = [
    ("mcp.json", mcp_json.parse),
    (".mcp.json", mcp_json.parse),
    ("claude_desktop_config.json", mcp_json.parse),
    (".claude-plugin/plugin.json", claude_plugin.parse),
    (".claude/settings.json", claude_settings.parse),
    ("**/.claude/skills/*/SKILL.md", claude_skill.parse),
    ("**/.claude/commands/**/*.md", _parse_repo_command),
]

# Cursor's repo-mode MCP and Skills surfaces (ADR-0044). Parsers are
# pre-bound via functools.partial so each still matches the single-Path
# ParserFn signature; the registry dispatch loop never needs to know
# host-tagging happened.
CURSOR_MANIFEST_REGISTRY: list[tuple[str, ParserFn]] = [
    (".cursor/mcp.json", functools.partial(mcp_json.parse, runtime_hosts=["cursor"])),
    (
        "**/.cursor/skills/*/SKILL.md",
        functools.partial(claude_skill.parse, runtime_hosts=["cursor"]),
    ),
    (
        "**/.agents/skills/*/SKILL.md",
        # Not ["cursor", "codex"]: Codex isn't a registered host in this
        # plan (no HOSTS["codex"] entry exists), so a scan can never
        # actually select it — tagging refs with a host the scan didn't
        # verify contradicts this project's evidence-over-inference
        # discipline, even though .agents/skills genuinely is Codex-
        # readable per the spec's own research. It also collides with
        # the spec's Identity section, which names subagents as the
        # *only* confirmed case where one occurrence needs multiple
        # runtime_hosts — .agents/skills getting the same treatment here
        # wasn't reconciled against that. Revisit together with subagents
        # once Codex is a registered host (ADR-0045's "When to revisit"
        # names the Codex trigger; not resolved by this design).
        functools.partial(claude_skill.parse, runtime_hosts=["cursor"]),
    ),
    (
        "**/.cursor/commands/**/*.md",
        functools.partial(
            claude_command_agent.parse_file, kind="command", runtime_hosts=["cursor"]
        ),
    ),
    (
        ".cursor-plugin/plugin.json",
        functools.partial(claude_plugin.parse, runtime_hosts=["cursor"]),
    ),
]

# Bare (non-host-scoped) basenames more than one host's convention can claim.
# Only directory context tells them apart, so the bare-basename branch of
# `registry_pattern_matches` defers to `owning_host` for these.
_HOST_AMBIGUOUS_BASENAMES = frozenset({"mcp.json", ".mcp.json"})

# Directory names that own their own `plugin.json` via a registry pattern
# (`.claude-plugin/plugin.json`, `.cursor-plugin/plugin.json`). A bare
# `plugin.json` nested under one of these is that native format's manifest,
# never an Agent Plugins root — skip it in the content-based dispatch below
# regardless of whether the native parse actually succeeds.
_NATIVE_PLUGIN_CONFIG_DIRS = frozenset({".claude-plugin", ".cursor-plugin"})

# Which host each native plugin manifest directory belongs to — the parser
# walk's equivalent of graph dispatch's `_plugin_parser_for_path` ownership.
_NATIVE_PLUGIN_MANIFEST_OWNERS = {".claude-plugin": "claude-code", ".cursor-plugin": "cursor"}


def _plugin_manifest_realizes(manifest: Path) -> bool:
    """Whether `claude_plugin.parse` would emit a plugin self ref for this
    manifest: valid JSON object with a non-empty string `name`. Mirrors the
    graph's realization test (`_descend_into_plugin` succeeds iff the parser
    yields a plugin-typed ref) without paying for a full bundled-surface walk.
    """
    try:
        data = json.loads(manifest.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    name = data.get("name")
    return isinstance(name, str) and bool(name)


def _unselected_native_bundle_roots(root: Path, spec, selected_hosts: list[str]) -> list[Path]:
    """Resolved roots of native plugin bundles owned by an UNSELECTED host.

    Mirror of `build_graph`'s `unselected_host_plugin_roots` for the public
    parser walk: a bundle root is excluded when a native manifest owned by an
    unselected host exists there and no selected-host sibling manifest
    realizes. Without this, a Cursor bundle's bare root `mcp.json` matches
    Claude's bare pattern under `hosts=["claude-code"]` and its servers are
    inventoried (and counted in n_found) for a host the caller explicitly
    excluded — `build_graph` gained this boundary, but direct `parse_repo`
    consumers walk independently and need the same one.
    """
    roots: list[Path] = []
    for path in iter_unignored_files(root, spec):
        if path.name != "plugin.json":
            continue
        owner = _NATIVE_PLUGIN_MANIFEST_OWNERS.get(path.parent.name)
        if owner is None or owner in selected_hosts:
            continue
        bundle_root = path.parent.parent
        # Containment parity with the graph: _find_plugin_roots drops a
        # manifest symlink escaping its own bundle root before it can become
        # a candidate, so it proves no foreign boundary here either.
        if resolve_within(bundle_root, f"{path.parent.name}/plugin.json") is None:
            continue
        # Realization parity with the graph: a selected-host sibling manifest
        # that realizes keeps the bundle in scope; a malformed selected
        # sibling does not — the unselected candidate's mere presence still
        # proves a foreign bundle boundary.
        if any(
            other_owner in selected_hosts
            and (candidate := bundle_root / config_dir / "plugin.json").is_file()
            # A symlinked sibling manifest escaping the bundle must not decide
            # whether a foreign bundle stays in scope.
            and resolve_within(bundle_root, f"{config_dir}/plugin.json") is not None
            and _plugin_manifest_realizes(candidate)
            for config_dir, other_owner in _NATIVE_PLUGIN_MANIFEST_OWNERS.items()
        ):
            continue
        try:
            roots.append(bundle_root.resolve())
        except OSError:
            continue
    return roots


def _agent_plugin_bundle_roots(root: Path, spec, selected_hosts: list[str]) -> list[Path]:
    """Resolved roots of Agent Plugins bundles the inline fallback below will
    claim (same gate: `cursor` selected, containment holds, schema detects,
    manifest realizes).

    A bundle's root-level `mcp.json` must not ALSO match the bare Claude Code
    `mcp.json` pattern in the registry loop: `flatten_grouped`'s dedup key
    doesn't include `runtime_hosts`, so whichever route appends its group
    first would silently win, and the walk order between a `plugin.json` and
    its sibling `mcp.json` isn't something either the registry loop or the
    dedup step normalizes. Precomputing which bundles will claim their own
    `mcp.json` lets the registry loop skip it up front, so only the
    Cursor-tagged Agent Plugin route ever appends a group for that file.

    Requires `_plugin_manifest_realizes` (a non-empty string `name`), not just
    schema detection: a schema-tagged manifest missing `name` never emits a
    plugin self ref (`agent_plugins.parse`'s `if name:` guard), and the graph
    builder's `_realize_agent_plugin` attaches nothing for it — including its
    sibling `mcp.json` — falling through to the standalone Claude Code walk.
    Without this check, this pre-pass claimed the bundle root purely on
    schema match, so the bare pattern's `mcp.json` was skipped here while the
    inline fallback below still called `agent_plugins.parse` and produced a
    Cursor-tagged ref for the same file — reintroducing the two-route
    dedup race this function exists to prevent, just for the malformed case.
    """
    if "cursor" not in selected_hosts:
        return []
    roots: list[Path] = []
    for path in iter_unignored_files(root, spec):
        if path.name != "plugin.json" or path.parent.name in _NATIVE_PLUGIN_CONFIG_DIRS:
            continue
        if resolve_within(path.parent, "plugin.json") is None:
            continue
        if not agent_plugins.is_agent_plugins_manifest(path):
            continue
        if not _plugin_manifest_realizes(path):
            continue
        try:
            roots.append(path.parent.resolve())
        except OSError:
            continue
    return roots


def resolve_host_selection(hosts: list[str] | None) -> list[str]:
    """Resolve `hosts` to a concrete, order-preserving, duplicate-free
    list of *known* host IDs, and raise if two of them claim the
    identical registry pattern string.

    Combines three concerns handled inconsistently before this fix:
    unknown-ID rejection, deduplication, and collision rejection.

    **Unknown-ID rejection:** `tools/scan.py`'s CLI already rejects an
    unrecognized `--host` value with a clear `click.BadParameter` before
    calling either public function below, but a direct caller bypassing
    the CLI — a test, or a future non-CLI consumer — passing
    `hosts=["typo"]` previously got no error at all: `_active_registry`/
    graph dispatch simply found no adapter for `"typo"` and silently
    contributed nothing for it, while `HOST_AGNOSTIC_REGISTRY`'s
    dependency-manifest parsers still ran normally — producing a scan
    that *looks* like a legitimate, complete result rather than an
    obviously-wrong one, with no signal that the requested host was
    never recognized.

    **Deduplication:** `tools/scan.py`'s CLI already dedupes repeated/
    comma-separated `--host` values, but `_active_registry` and
    `build_graph` are themselves public, and a direct caller passing
    `hosts=["cursor", "cursor"]` would previously make `_active_registry`
    extend its registry with Cursor's `manifest_registry` twice,
    double-counting `n_found` and producing duplicate refs for the same
    file, while graph dispatch stayed correct — its first-match loop is
    idempotent for a repeated ID. That's the same "accounting
    over-counts, graph doesn't" divergence class the pattern-collision
    check below exists to prevent, from a different cause.

    **Collision rejection:** a bare (non-host-scoped) pattern like
    "mcp.json" carries no path information distinguishing which host
    owns a matching file. Reusing one verbatim across two *distinct*,
    simultaneously selected hosts is genuinely ambiguous, not just
    under-specified. Cursor's own pattern (`.cursor/mcp.json`) never
    collides with Claude's bare filenames precisely because it's
    host-scoped in the path itself — a future host wanting to reuse a
    bare, already-allowlisted pattern *alongside* its existing owner
    must do the same rather than share the identical string.

    Called from both `_active_registry` and `build_graph`'s repo-mode
    entry point: one implementation, so the two mechanisms can't
    silently disagree about any of the three concerns, only fail or
    normalize identically.
    """
    from tools.hosts import HOSTS

    selected = list(dict.fromkeys(hosts if hosts is not None else HOSTS.keys()))
    if hosts is not None:
        unknown = [host_id for host_id in selected if host_id not in HOSTS]
        if unknown:
            known = ", ".join(sorted(HOSTS))
            raise ValueError(f"unknown host(s) {unknown!r}; known hosts: {known}")
    owners: dict[str, str] = {}
    for host_id in selected:
        adapter = HOSTS.get(host_id)
        if adapter is None:
            continue
        for pattern, _parser in adapter.manifest_registry:
            if pattern in owners and owners[pattern] != host_id:
                raise ValueError(
                    f"registry pattern {pattern!r} is claimed by both "
                    f"{owners[pattern]!r} and {host_id!r} — reusing a "
                    "pattern verbatim across two simultaneously selected "
                    "hosts is ambiguous. Give the new host a distinct, "
                    "host-scoped pattern (e.g. the '.newhost/mcp.json' "
                    "shape Cursor already uses), or move the parser to "
                    "HOST_AGNOSTIC_REGISTRY if it's genuinely meant to be "
                    "shared across hosts."
                )
            owners[pattern] = host_id
    return selected


def _active_registry(hosts: list[str] | None) -> list[tuple[str, ParserFn]]:
    from tools.hosts import HOSTS  # deferred: tools.hosts imports from this module

    selected = resolve_host_selection(hosts)
    registry = list(HOST_AGNOSTIC_REGISTRY)
    for host_id in selected:
        adapter = HOSTS.get(host_id)
        if adapter is not None:
            registry.extend(adapter.manifest_registry)
    return registry


def registry_pattern_matches(path: Path, root: Path, pattern: str) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path

    if "/" not in pattern and "*" not in pattern:
        # A `.cursor`-nested `mcp.json` must stop matching Claude's bare
        # pattern entirely: the dispatch loop checks every pattern, not just
        # the first match, so without this the same file would be parsed
        # twice — once mistagged claude-code — inflating n_found.
        if pattern in _HOST_AMBIGUOUS_BASENAMES and owning_host(rel) != "claude-code":
            return False
        return rel.name == pattern

    rel_parts = rel.parts
    rel_posix = rel.as_posix()
    if pattern in {
        ".claude-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
        ".claude/settings.json",
        ".cursor/mcp.json",
    }:
        return rel_posix == pattern or rel_posix.endswith(f"/{pattern}")

    skill_dir_name = _skill_pattern_config_dir(pattern)
    if skill_dir_name is not None:
        return _skill_path_matches(rel_parts, skill_dir_name)

    command_config_dir = _COMMAND_PATTERN_CONFIG_DIRS.get(pattern)
    if command_config_dir is not None:
        return rel.suffix == ".md" and any(
            rel_parts[i] == command_config_dir
            and i + 2 < len(rel_parts)
            and rel_parts[i + 1] == "commands"
            for i in range(len(rel_parts) - 2)
        )

    return rel.match(pattern)


_COMMAND_PATTERN_CONFIG_DIRS = {
    "**/.claude/commands/**/*.md": ".claude",
    "**/.cursor/commands/**/*.md": ".cursor",
}


_SKILL_PATTERN_CONFIG_DIRS = {
    "**/.claude/skills/*/SKILL.md": ".claude",
    "**/.cursor/skills/*/SKILL.md": ".cursor",
    "**/.agents/skills/*/SKILL.md": ".agents",
}


def _skill_pattern_config_dir(pattern: str) -> str | None:
    return _SKILL_PATTERN_CONFIG_DIRS.get(pattern)


def _skill_path_matches(rel_parts: tuple[str, ...], config_dir: str) -> bool:
    if len(rel_parts) < 4 or rel_parts[-1] != "SKILL.md":
        return False
    return any(
        rel_parts[i] == config_dir
        and i + 3 < len(rel_parts)
        and rel_parts[i + 1] == "skills"
        and i + 3 == len(rel_parts) - 1
        for i in range(len(rel_parts) - 3)
    )


def _filter_secondary_refs(
    refs: list[ComponentRef],
    primary: Path,
    root: Path,
    spec,
) -> list[ComponentRef]:
    """Drop refs whose source_manifest is a secondary gitignored file.

    Some parsers (e.g. claude_plugin when mcpServers is a string path)
    follow references to other files on disk. Those secondary files bypass
    the rglob filter applied in parse_repo_grouped, so we re-apply the
    same spec check here. Refs from the primary file are always kept.

    When spec=None (include_gitignored=True), is_ignored only blocks .git/
    paths — consistent with the rglob-hit filtering logic above.
    """
    primary_resolved = primary.resolve()
    root_resolved = root.resolve()
    out: list[ComponentRef] = []
    for r in refs:
        if not r.source_manifest:
            out.append(r)
            continue
        src = Path(r.source_manifest).resolve()
        if src == primary_resolved:
            out.append(r)
            continue
        try:
            rel = src.relative_to(root_resolved)
        except ValueError:
            out.append(r)  # outside root; path safety enforced by the parser
            continue
        if not is_ignored(rel, spec):
            out.append(r)
    return out


def parse_repo_grouped(
    root: Path,
    include_gitignored: bool = False,
    hosts: list[str] | None = None,
) -> tuple[list[tuple[Path, list[ComponentRef]]], int]:
    """Walk `root` and return (per-manifest results, total paths matched).

    The second element counts every path that matched a registry pattern AND
    survived `.gitignore` filtering. Callers use this to distinguish "target
    had no manifests at all" (n_found == 0) from "target had manifests that
    all failed to parse" (n_found > 0 but grouped is empty).

    By default, paths matching entries in `<root>/.gitignore` are excluded —
    typical repos pull `node_modules/`, `.venv/`, `dist/`, etc. into rglob
    hits and emit noisy/wrong findings (a vendored `package.json` deep inside
    `node_modules/` shouldn't be attributed to the host repo). Set
    `include_gitignored=True` to walk those anyway (e.g., to audit a vendored
    dependency tree). `.git/` is always skipped.

    Per-path parse failures are silently dropped — these parsers run against
    arbitrary user repos and one malformed file should not abort the rest of
    the scan. Manifests with zero components are still included so consumers
    can see the file was visited.

    Per-manifest groups preserve duplicates intentionally — verbose output
    should show what each manifest declared, even if another manifest's parse
    path discovered the same component. Use `flatten_grouped` (or `parse_repo`)
    when a deduplicated cross-manifest ref list is needed for matching/SARIF;
    those callers want one finding per logical component, not per discovery
    path.
    """
    from tools.hosts import HOSTS  # deferred: tools.hosts imports from this module

    spec = None if include_gitignored else load_gitignore_spec(root)
    grouped: list[tuple[Path, list[ComponentRef]]] = []
    n_found = 0
    registry = _active_registry(hosts)
    selected_hosts = resolve_host_selection(hosts)
    # Only pay for the boundary pre-pass when some known host is actually
    # unselected — the default all-hosts walk can't have foreign bundles.
    excluded_bundle_roots: list[Path] = []
    if any(host_id not in selected_hosts for host_id in HOSTS):
        excluded_bundle_roots = _unselected_native_bundle_roots(root, spec, selected_hosts)

    def _under_excluded_bundle(path: Path) -> bool:
        if not excluded_bundle_roots:
            return False
        try:
            resolved = path.resolve()
        except OSError:
            return False
        return any(resolved.is_relative_to(r) for r in excluded_bundle_roots)

    agent_plugin_bundle_roots = set(_agent_plugin_bundle_roots(root, spec, selected_hosts))

    def _is_agent_plugin_bundle_root_mcp(path: Path) -> bool:
        if not agent_plugin_bundle_roots or path.name != "mcp.json":
            return False
        try:
            resolved_parent = path.parent.resolve()
        except OSError:
            return False
        return resolved_parent in agent_plugin_bundle_roots

    for path in iter_unignored_files(root, spec):
        if _under_excluded_bundle(path):
            continue
        # A native plugin manifest that is a symlink escaping its own bundle
        # root must not match its registry pattern: parsing it mints plugin
        # self-identity from a document outside the bundle (graph dispatch's
        # _find_plugin_roots applies the same containment).
        if (
            path.name == "plugin.json"
            and path.parent.name in _NATIVE_PLUGIN_CONFIG_DIRS
            and resolve_within(path.parent.parent, f"{path.parent.name}/plugin.json") is None
        ):
            continue
        matched = False
        for pattern, parser in registry:
            if not registry_pattern_matches(path, root, pattern):
                continue
            # An Agent Plugins bundle's own root mcp.json is claimed below by
            # the Cursor-tagged fallback branch — the bare Claude Code
            # pattern must not also claim it (see
            # `_agent_plugin_bundle_roots`).
            if pattern == "mcp.json" and _is_agent_plugin_bundle_root_mcp(path):
                continue
            matched = True
            n_found += 1
            try:
                refs = parser(path)
                refs = _filter_secondary_refs(refs, path, root, spec)
                grouped.append((path, refs))
            except Exception:
                continue
        if (
            not matched
            and path.name == "plugin.json"
            and path.parent.name not in _NATIVE_PLUGIN_CONFIG_DIRS
            and "cursor" in selected_hosts
            # A symlinked plugin.json escaping its own bundle root (the
            # manifest's parent) must not be schema-detected or parsed —
            # same containment as graph dispatch's _find_agent_plugin_roots.
            and resolve_within(path.parent, "plugin.json") is not None
            and agent_plugins.is_agent_plugins_manifest(path)
            # Realization parity with `_realize_agent_plugin`/
            # `_agent_plugin_bundle_roots`: a schema-tagged manifest missing
            # `name` must not claim the bundle at all, including its sibling
            # `mcp.json` — that file is left for the bare Claude Code pattern
            # above, matching what the graph builder falls through to.
            and _plugin_manifest_realizes(path)
        ):
            n_found += 1
            try:
                refs = agent_plugins.parse(path, runtime_hosts=["cursor"])
                refs = _filter_secondary_refs(refs, path, root, spec)
                grouped.append((path, refs))
            except Exception:
                continue

    # Subagents (`.claude/agents/**/*.md`, `.cursor/agents/**/*.md`) are no
    # longer registry-driven: pairing a Cursor override with its Claude
    # counterpart needs the resolver's cross-file precedence logic, which a
    # single-path registry pattern can't express (Task 12).
    # deferred: subagent_precedence imports from this module
    from tools.subagent_precedence import (
        group_occurrences_by_manifest,
        resolve_subagent_occurrences,
    )

    for manifest_path, manifest_refs in group_occurrences_by_manifest(
        resolve_subagent_occurrences(root, selected_hosts)
    ):
        if _under_excluded_bundle(manifest_path):
            continue
        try:
            rel = manifest_path.relative_to(root)
        except ValueError:
            rel = manifest_path
        if is_ignored(rel, spec):
            continue
        n_found += 1
        grouped.append((manifest_path, manifest_refs))

    return grouped, n_found


def flatten_grouped(
    grouped: list[tuple[Path, list[ComponentRef]]],
) -> list[ComponentRef]:
    """Flatten per-manifest groups into a deduplicated ref list.

    The same logical component can be discovered via multiple registry paths
    — e.g., a `.mcp.json` walked directly AND followed indirectly through a
    `.claude-plugin/plugin.json` whose `mcpServers` is the string path
    `"./.mcp.json"`. Both routes emit identical refs (same source_manifest +
    source_locator + identity). Without dedup, matching produces duplicate
    findings and SARIF emits duplicate results.

    Dedup key intentionally excludes `extra` (a dict, so unhashable; also
    discovery-path-dependent in some cases). What identifies a logical component
    for matching is the (where, what) tuple:
    (source_manifest, source_locator, ecosystem, name, version, component_identity).
    The first route to discover a key wins; attribution is no longer a ref field
    (it is derived from the graph), so there is no attributed-vs-unattributed
    preference to apply.
    """
    refs: list[ComponentRef] = []
    seen: set[tuple] = set()
    for _, group in grouped:
        for r in group:
            # Resolve source_manifest to an absolute path so that relative
            # and absolute references to the same file collapse to the same
            # key. Without this, `--target .` produces a relative path from
            # the direct rglob hit while _parse_mcp_servers_from_plugin_json
            # calls Path.resolve() internally, yielding different strings for
            # the same file and breaking dedup.
            manifest_key = str(Path(r.source_manifest).resolve()) if r.source_manifest else ""
            key = (
                manifest_key,
                r.source_locator,
                r.ecosystem,
                r.name,
                r.version,
                r.component_identity,
            )
            if key in seen:
                continue
            seen.add(key)
            refs.append(r)
    return refs


def parse_repo(
    root: Path, include_gitignored: bool = False, hosts: list[str] | None = None
) -> list[ComponentRef]:
    """Walk `root` and return deduplicated ComponentRefs from all known manifests."""
    grouped, _ = parse_repo_grouped(root, include_gitignored=include_gitignored, hosts=hosts)
    return flatten_grouped(grouped)
