"""Manifest parser registry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple, Union

from pathspec import GitIgnoreSpec

from tools.component_ref import ComponentRef
from tools.parsers import (
    agent_plugins,
    bun_lock,
    claude_command_agent,
    claude_plugin,
    claude_settings,
    claude_skill,
    codex_config,
    hooks_json,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    uv_lock,
)
from tools.parsers.gitignore import is_ignored, iter_unignored_files, load_gitignore_spec

if TYPE_CHECKING:
    # Annotation-only: `tools.repo_surface` imports THIS module, so a runtime
    # import here would be a genuine cycle.
    from tools.repo_surface import RepoSurface

ParserFn = Callable[[Path], list[ComponentRef]]
# `(path, root, spec)`: `root`/`spec` carry the same gitignore context the
# walk itself resolved `path` under, so a guard that needs to check a
# SIBLING candidate's gitignore status (not just `path`'s own — that's
# already been filtered by the walk before the guard ever runs) can match
# composition's own gitignore-aware resolution instead of assuming every
# candidate on disk is live. Guards that don't need this ignore the trailing
# args; `root`/`spec` default to `None` so a guard remains callable directly
# with just a path (`is_agent_plugins_manifest` is used this way outside the
# registry).
GuardFn = Callable[[Path, Path, "GitIgnoreSpec | None"], bool]


class ManifestPattern(NamedTuple):
    """One registry entry: a glob, its parser, and an optional guard.

    A `NamedTuple` (not a dataclass) so `manifest_patterns` tuples stay
    hashable — `tools/scan.py` and `tools/bom_cli.py` cache on
    `(scan_root, kind.manifest_patterns)`.
    """

    pattern: str
    parser: ParserFn
    guard: GuardFn | None = None


RegistryEntry = Union[ManifestPattern, tuple[str, ParserFn]]


def _parse_repo_command(path: Path) -> list[ComponentRef]:
    return claude_command_agent.parse_file(path, kind="command")


def _parse_repo_agent(path: Path) -> list[ComponentRef]:
    return claude_command_agent.parse_file(path, kind="agent")


# The five dependency manifests shared by every host — not Claude-Code- or
# Cursor-specific.
HOST_AGNOSTIC_REGISTRY: list[ManifestPattern] = [
    ManifestPattern("package.json", package_json.parse),
    ManifestPattern("pyproject.toml", pyproject_toml.parse),
    ManifestPattern("package-lock.json", package_lock_json.parse),
    ManifestPattern("uv.lock", uv_lock.parse),
    ManifestPattern("bun.lock", bun_lock.parse),
]

# Everything else in the pre-split REGISTRY, unchanged and in order. The
# `.claude-plugin/plugin.json` and `.claude/settings.json` patterns are
# re-anchored to `**/...` here: git-wildmatch anchors a slashed pattern at the
# root, but the hand-rolled matcher these patterns used to run through
# special-cased them to mean "at any depth" — the `**/` prefix is required to
# keep that meaning under `pathspec`.
CLAUDE_CODE_MANIFEST_REGISTRY: list[ManifestPattern] = [
    ManifestPattern("mcp.json", mcp_json.parse),
    ManifestPattern(".mcp.json", mcp_json.parse),
    # Claude Desktop user-config: same JSON shape as `mcp.json`
    # (`mcpServers` map of stdio launches), different filename. Reuse
    # the same parser; the filename pattern is the only addition.
    ManifestPattern("claude_desktop_config.json", mcp_json.parse),
    ManifestPattern("**/.claude-plugin/plugin.json", claude_plugin.parse),
    ManifestPattern("**/.claude/settings.json", claude_settings.parse),
    # Plan 008: agent-component inventory in repo mode. These
    # surfaces emit the same ecosystems as endpoint mode; parentage is set by
    # the graph edge, not stored on the refs.
    ManifestPattern("**/.claude/skills/*/SKILL.md", claude_skill.parse),
    ManifestPattern("**/.claude/commands/**/*.md", _parse_repo_command),
    ManifestPattern("**/.claude/agents/**/*.md", _parse_repo_agent),
]

# Extension/root sets mirror `tools/cursor_commands.py`/`tools/cursor_subagents.py`
# exactly (`.codex`/`.agents` are deliberately excluded — neither resolver reads
# commands or agents from them), so this registry's (n_found, n_failed) counts
# never disagree with what composition actually discovers and parses.
_CURSOR_COMMAND_EXTENSIONS = (".md", ".txt")
_CURSOR_AGENT_EXTENSIONS = (".md", ".mdc", ".markdown")
_CURSOR_COMMAND_AGENT_DIRS = (".cursor", ".claude")

# Mirrors `_MAX_TRAVERSAL_DEPTH` in `tools/cursor_commands.py` and
# `tools/cursor_subagents.py`: both resolvers drop a command/subagent whose
# path relative to its `commands`/`agents` root exceeds 10 segments. The glob
# patterns below can't express that limit, so a file past it would otherwise
# inflate `n_found`/`n_failed` for configuration composition never loads.
_CURSOR_COMMAND_AGENT_MAX_DEPTH = 10


def _within_cursor_traversal_depth(scope_dirname: str, root_dirname: str) -> GuardFn:
    def guard(path: Path, root: Path | None = None, spec: GitIgnoreSpec | None = None) -> bool:
        _ = (root, spec)
        parts = path.parts
        for i in range(len(parts) - 1, 0, -1):
            if parts[i] == root_dirname and parts[i - 1] == scope_dirname:
                return len(parts) - i - 1 <= _CURSOR_COMMAND_AGENT_MAX_DEPTH
        return True

    return guard


def _parse_repo_cursor_command(path: Path) -> list[ComponentRef]:
    return claude_command_agent.parse_file(
        path, kind="command", extensions=_CURSOR_COMMAND_EXTENSIONS
    )


def _parse_repo_cursor_agent(path: Path) -> list[ComponentRef]:
    # `strict=True` (parity with `tools.cursor_subagents._parse_isolated` and
    # `graph_build._add_endpoint_command_agents`'s `strict=kind == "agent"`):
    # `parse_repo_registry_counts` counts this call's raised exceptions
    # toward `n_failed`, which feeds `bom_cli`'s "N of M manifest(s) failed
    # to parse" warning and `evidence_gaps`. Without `strict=True`, malformed
    # frontmatter/`mcpServers`/`hooks` silently parses as an empty/partial
    # agent and coverage reports a clean parse for a file that dropped data.
    return claude_command_agent.parse_file(
        path, kind="agent", extensions=_CURSOR_AGENT_EXTENSIONS, strict=True
    )


def _parse_repo_agent_plugins(path: Path) -> list[ComponentRef]:
    # `strict=True`: this route's guard is `agent_plugins.is_agent_plugins_manifest`,
    # the same "`$schema` already confirmed to qualify" precondition
    # `_realize_agent_plugins_root` (`tools/graph_build_cursor.py`) runs under —
    # a `validate_manifest` failure past that guard is a real defect, not a
    # registry-guard miss. Non-strict here would let `parse_repo_grouped`/
    # `parse_repo_registry_counts` report a fatally invalid manifest as a
    # clean, empty, successfully-parsed unit instead of a `parse_failed`.
    return agent_plugins.parse(path, strict=True)


def _parse_repo_codex_config(path: Path) -> list[ComponentRef]:
    # Malformed TOML raises out of `codex_config.parse` regardless of a strict
    # flag, so a broken `config.toml` registers as `parse_failed` and reaches
    # `evidence_gaps` rather than counting as a clean, empty source unit.
    return codex_config.parse(path, strict=True)


def _parse_repo_codex_hooks(path: Path) -> list[ComponentRef]:
    # `strict=True` mirrors what composition does: `_add_codex_declared_hooks`
    # parses strictly, so a malformed hooks file must fail here too or the
    # count and the graph disagree about the same file.
    return hooks_json.parse_standalone_hooks(path, scope="project", strict=True)


def plugin_owned_roots(
    root: Path, surface: RepoSurface | None, *, include_gitignored: bool
) -> list[Path]:
    """Realized plugin roots whose content this walk must not claim, or `[]`
    when `surface` does not opt in (`excludes_plugin_owned_content`).

    Thin wrapper around `tools.graph_build_cursor.realized_plugin_roots`, the
    one place that computation is implemented (Cursor evidence detection in
    `tools/agent_kinds/cursor.py` reaches the same function), so registry
    accounting's exclusion can never drift from composition's own.

    Local import: `tools.graph_build_cursor` imports `tools.parsers` at
    module scope, so importing it back at this module's top level would be
    circular. Deferring the import to call time breaks the cycle — the same
    technique `tools/agent_kinds/cursor.py` already uses for the sibling
    `tools.graph_build` import.
    """
    if surface is None or not surface.excludes_plugin_owned_content:
        return []
    from tools.graph_build_cursor import realized_plugin_roots

    return realized_plugin_roots(root, surface, include_gitignored=include_gitignored)


def _entry_claims(
    path: Path,
    root: Path,
    entry: ManifestPattern,
    owned_roots: Sequence[Path],
    surface: RepoSurface | None = None,
    spec: GitIgnoreSpec | None = None,
) -> bool:
    """Whether `entry` is the registry route for `path`.

    Every walk in this module tests a candidate path through here, which is
    what makes plugin-ownership exclusion structural rather than a rule each
    walk remembers to apply. An earlier form gated the exclusion on an
    allowlist of three plugin-manifest patterns, so a bundled fixture
    `examples/.cursor/mcp.json` or `.cursor/commands/demo.md` was still
    counted and parsed — content composition never loads, inflating
    `source_unit_count` and able to register a false `parse_failed`.
    Ownership is a property of the PATH, so it is tested for every pattern.

    `root`/`spec` are the walk's own gitignore context, passed through to
    `entry.guard` for the rare guard that needs to judge a sibling candidate
    rather than just `path` (see `_is_resolved_codex_plugin_format`).
    """
    if not _pattern_matches(path, root, entry.pattern):
        return False
    if entry.guard is not None and not entry.guard(path, root, spec):
        return False
    if owned_roots and surface is not None:
        from tools.graph_build_cursor import is_owned_by_realized_plugin

        if is_owned_by_realized_plugin(path, list(owned_roots), surface):
            return False
    return True


# Cursor's manifest surface. Deliberately no bare `mcp.json`/`.mcp.json`:
# Cursor's direct MCP surface is the path-scoped `.cursor/mcp.json`; bundle
# roots are reached only through the plugin route. A bare pattern here would
# re-create the cross-format collision the per-agent-graph model exists to
# prevent.
CURSOR_MANIFEST_REGISTRY: list[ManifestPattern] = [
    ManifestPattern("**/.cursor/mcp.json", mcp_json.parse),
    # `**/SKILL.md`, not `*/SKILL.md`: Cursor walks its skill roots
    # RECURSIVELY (docs/specs/cursor-agent-kind.md, Skills), so a skill at
    # `.cursor/skills/group/tool/SKILL.md` is composed. Matching only an
    # immediate child would undercount `source_unit_count` and leave a
    # malformed nested skill unable to register a parse failure.
    #
    # This is the opposite of an Agent Plugins bundle's `skills/`, which the
    # standard forbids recursing into — that surface is walked by
    # `agent_plugins.parse`, never by this registry.
    ManifestPattern("**/.cursor/skills/**/SKILL.md", claude_skill.parse),
    ManifestPattern("**/.agents/skills/**/SKILL.md", claude_skill.parse),
    ManifestPattern("**/.claude/skills/**/SKILL.md", claude_skill.parse),
    ManifestPattern("**/.codex/skills/**/SKILL.md", claude_skill.parse),
    *(
        ManifestPattern(
            f"**/{dirname}/commands/**/*{ext}",
            _parse_repo_cursor_command,
            _within_cursor_traversal_depth(dirname, "commands"),
        )
        for dirname in _CURSOR_COMMAND_AGENT_DIRS
        for ext in _CURSOR_COMMAND_EXTENSIONS
    ),
    *(
        ManifestPattern(
            f"**/{dirname}/agents/**/*{ext}",
            _parse_repo_cursor_agent,
            _within_cursor_traversal_depth(dirname, "agents"),
        )
        for dirname in _CURSOR_COMMAND_AGENT_DIRS
        for ext in _CURSOR_AGENT_EXTENSIONS
    ),
    ManifestPattern("**/.cursor-plugin/plugin.json", claude_plugin.parse),
    # Cursor's ordered candidate list includes Claude Code's manifest, so a
    # bundle carrying only `.claude-plugin/plugin.json` still realizes in the
    # Cursor graph. Without this route its manifest never counts toward
    # Cursor's `source_unit_count` and a malformed one cannot contribute a
    # parse failure or an evidence gap.
    ManifestPattern("**/.claude-plugin/plugin.json", claude_plugin.parse),
    ManifestPattern(
        "plugin.json", _parse_repo_agent_plugins, agent_plugins.is_agent_plugins_manifest
    ),
]


def _is_resolved_codex_plugin_format(manifest_dir: str) -> GuardFn:
    """Claim a Codex plugin-root candidate only if it's the one
    `_resolve_plugin_format` actually picks for that root.

    Composition reads exactly one manifest per plugin root — the first
    candidate in `CODEX_SURFACE.plugin_formats` that both exists and
    qualifies (ADR-0053: "first qualifying candidate", not first path-shape
    match). Without this guard, a root carrying both `.codex-plugin/plugin.json`
    and `.claude-plugin/plugin.json` has BOTH claimed and parsed independently
    by this registry: `source_unit_count` double-counts one real manifest, and
    a malformed *unused* fallback (or a `.codex-plugin` candidate that fails
    `detect` but still happens to parse) can register a `parse_failed` for a
    file composition never reads — in either direction, the count disagrees
    with what `_add_codex_declared_config_mcps`'s plugin branch actually
    composes.

    `resolve_plugin_format` must see the SAME `eval_root`/`spec` gitignore
    context `_find_plugin_roots` resolves this root's plugin format under
    (`root`/`spec` here are exactly that: the scan root and gitignore spec
    the walk calling this guard is using) — passing none, as an earlier
    version of this guard did, makes it treat a gitignored higher-precedence
    candidate as present. A root where `.codex-plugin/plugin.json` is
    gitignored but `.claude-plugin/plugin.json` is not would then have this
    guard pick the invisible `.codex-plugin` candidate and reject the visible
    `.claude-plugin` one composition actually reads — the exact double-count/
    phantom-failure bug this guard exists to prevent, just for the opposite
    candidate.

    Local import: `tools.graph_build` imports `tools.parsers` at module scope
    (same cycle `plugin_owned_roots` already breaks this way for
    `tools.graph_build_cursor`).
    """

    def guard(path: Path, root: Path | None = None, spec: GitIgnoreSpec | None = None) -> bool:
        from tools.graph_build import resolve_plugin_format
        from tools.repo_surface import CODEX_SURFACE

        plugin_root = path.parent.parent
        fmt = resolve_plugin_format(plugin_root, CODEX_SURFACE, eval_root=root, spec=spec)
        return fmt is not None and fmt.manifest_dir == manifest_dir

    return guard


# Codex's manifest surface. Deliberately NO bare `mcp.json`/`.mcp.json`:
# Codex's MCP servers are declared INSIDE `config.toml` as `[mcp_servers.*]`,
# never as a standalone manifest, so a bare pattern here would claim files
# Codex does not read and re-create the cross-format collision the
# per-agent-graph model exists to prevent.
#
# There is also no commands surface, and no `**/.codex/agents/**` route:
# `.codex/agents` has zero references in the audited binary, so a project
# cannot declare a Codex subagent. Subagents are user-scope
# `<root>/agents/*.toml`, which is endpoint state rather than repo content and
# therefore not a repo-mode registry entry at all.
CODEX_MANIFEST_REGISTRY: list[ManifestPattern] = [
    ManifestPattern("**/.codex/config.toml", _parse_repo_codex_config),
    ManifestPattern("**/.codex/hooks.json", _parse_repo_codex_hooks),
    # `**/SKILL.md`, not `*/SKILL.md`: `descend` walks Codex's skill roots
    # recursively via `CODEX_SURFACE`, so counting only immediate children
    # would undercount `source_unit_count` and leave a malformed nested skill
    # unable to register a parse failure.
    ManifestPattern("**/.codex/skills/**/SKILL.md", claude_skill.parse),
    # The cross-tool convention directory Codex also reads (ADR-0058), so its
    # skills count toward Codex's own source units.
    ManifestPattern("**/.agents/skills/**/SKILL.md", claude_skill.parse),
    # Guarded: claim `.codex-plugin/plugin.json` only when it's the format
    # `_resolve_plugin_format` resolves for its root — see
    # `_is_resolved_codex_plugin_format`.
    ManifestPattern(
        "**/.codex-plugin/plugin.json",
        claude_plugin.parse,
        _is_resolved_codex_plugin_format(".codex-plugin"),
    ),
    # Codex's ordered candidate list includes Claude Code's manifest, so a
    # bundle carrying only `.claude-plugin/plugin.json` still realizes in the
    # Codex graph. Without this route its manifest never counts toward Codex's
    # `source_unit_count` and a malformed one cannot contribute an evidence gap.
    # Guarded the same way as the `.codex-plugin` candidate above, so a root
    # carrying both never double-counts or double-fails the one manifest
    # composition actually reads.
    ManifestPattern(
        "**/.claude-plugin/plugin.json",
        claude_plugin.parse,
        _is_resolved_codex_plugin_format(".claude-plugin"),
    ),
]

# Compat alias: today's flat registry, kept byte-identical in content so
# `parse_repo`/`parse_repo_grouped` no-arg defaults, and
# `claude_code.KIND.manifest_patterns == tuple(REGISTRY)`, are unaffected.
REGISTRY: list[ManifestPattern] = [*HOST_AGNOSTIC_REGISTRY, *CLAUDE_CODE_MANIFEST_REGISTRY]


def _normalize_registry_entry(entry: RegistryEntry) -> ManifestPattern:
    if isinstance(entry, ManifestPattern):
        return entry
    pattern, parser = entry
    return ManifestPattern(pattern, parser)


_pattern_cache: dict[str, GitIgnoreSpec] = {}


def _compiled_pattern(pattern: str) -> GitIgnoreSpec:
    # Same shape as `gitignore.py`'s `load_gitignore_spec`/`is_ignored`: a
    # `GitIgnoreSpec` compiled from one pattern line, matched with
    # `match_file`. `GitWildMatchPattern` is the single-pattern class but is
    # deprecated in the installed pathspec version; `GitIgnoreSpec` is the
    # non-deprecated equivalent this repo already uses elsewhere.
    compiled = _pattern_cache.get(pattern)
    if compiled is None:
        compiled = GitIgnoreSpec.from_lines([pattern])
        _pattern_cache[pattern] = compiled
    return compiled


def _pattern_matches(path: Path, root: Path, pattern: str) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    # Normalize a Windows-style backslash path before matching — git-wildmatch
    # regexes are built assuming forward-slash-separated components.
    rel_posix = rel.as_posix().replace("\\", "/")
    return bool(_compiled_pattern(pattern).match_file(rel_posix))


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
    *,
    registry: Sequence[RegistryEntry] = REGISTRY,
    surface: RepoSurface | None = None,
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

    `registry` defaults to the global flat registry. A caller passes a kind's own
    `manifest_patterns` instead to walk this tree against just that kind's
    surface — "the flat manifest registry splits per kind, reached through a
    surface" — so a repo declaring two kinds counts each kind's own manifests
    rather than the union.
    """
    spec = None if include_gitignored else load_gitignore_spec(root)
    normalized_registry = [_normalize_registry_entry(entry) for entry in registry]
    owned_roots = plugin_owned_roots(root, surface, include_gitignored=include_gitignored)
    grouped: list[tuple[Path, list[ComponentRef]]] = []
    n_found = 0
    for path in iter_unignored_files(root, spec):
        for entry in normalized_registry:
            if not _entry_claims(path, root, entry, owned_roots, surface, spec):
                continue
            n_found += 1
            try:
                refs = entry.parser(path)
                refs = _filter_secondary_refs(refs, path, root, spec)
                grouped.append((path, refs))
            except Exception:
                pass
            # One-file-one-route: the walker, not registry hygiene, enforces
            # that a path is claimed by at most one pattern.
            break
    return grouped, n_found


def parse_repo_registry_counts(
    root: Path,
    registries: Mapping[str, Sequence[RegistryEntry]],
    include_gitignored: bool = False,
    *,
    surfaces: Mapping[str, RepoSurface | None] | None = None,
) -> tuple[dict[str, tuple[int, int]], tuple[int, int]]:
    """One filesystem walk producing every registry's own (n_found, n_failed)
    plus the (n_found, n_failed) of their union — each path counted once
    toward the union no matter how many registries' patterns match it.

    Two kinds sharing manifest surface (e.g. the five host-agnostic
    dependency manifests) each need their own count for their own
    coverage — a Cursor parse failure must not degrade the Claude agent's
    coverage — but a *scan-wide* total must count a shared `package.json`
    once, not once per kind that declares it. Walking `root` once and
    checking every registry per path, instead of once per registry, is what
    keeps the two accounting layers about the same paths from disagreeing.
    """
    spec = None if include_gitignored else load_gitignore_spec(root)
    normalized = {
        key: [_normalize_registry_entry(entry) for entry in registry]
        for key, registry in registries.items()
    }
    # Per kind, not once for the walk: ownership depends on which plugin
    # formats the kind realizes, so two kinds sharing a root can disagree
    # about whether a given directory is a plugin — and each must be judged
    # against its own composition.
    owned_by_key = {
        key: plugin_owned_roots(
            root, (surfaces or {}).get(key), include_gitignored=include_gitignored
        )
        for key in normalized
    }
    per_key_found = dict.fromkeys(registries, 0)
    per_key_failed = dict.fromkeys(registries, 0)
    union_found = 0
    union_failed = 0
    for path in iter_unignored_files(root, spec):
        union_matched = False
        union_ok = True
        for key, entries in normalized.items():
            for entry in entries:
                if not _entry_claims(
                    path, root, entry, owned_by_key[key], (surfaces or {}).get(key), spec
                ):
                    continue
                per_key_found[key] += 1
                try:
                    entry.parser(path)
                    ok = True
                except Exception:
                    ok = False
                    per_key_failed[key] += 1
                if not union_matched:
                    union_matched = True
                    union_ok = ok
                # One-file-one-route within this registry, mirroring
                # `parse_repo_grouped`.
                break
        if union_matched:
            union_found += 1
            if not union_ok:
                union_failed += 1
    return (
        {key: (per_key_found[key], per_key_failed[key]) for key in registries},
        (union_found, union_failed),
    )


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


def parse_repo(root: Path, include_gitignored: bool = False) -> list[ComponentRef]:
    """Walk `root` and return deduplicated ComponentRefs from all known manifests."""
    grouped, _ = parse_repo_grouped(root, include_gitignored=include_gitignored)
    return flatten_grouped(grouped)
