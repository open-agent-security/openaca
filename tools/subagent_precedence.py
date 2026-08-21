"""Precedence-aware occurrence resolution for Claude/Cursor subagents.

Cursor's subagent compatibility read is unconditional: a `.claude/agents/*.md`
file is genuinely readable by Cursor with no `.cursor/agents/` copy, UNLESS
Cursor has its own same-relative-path override, in which case Cursor never
reads Claude's file at all (confirmed against Cursor's own subagent docs;
see docs/specs/multi-host-support.md's Subagents section and ADR-0045
Decision #4). This can't be expressed through the registry/pattern-matcher
mechanism the rest of this design uses, since it requires inspecting a
sibling path before deciding one file's occurrence count. "Same subagent" is
matched by relative file path under the agents directory, not frontmatter
`name:` (ADR-0045 Decision #4) — the more literal, verifiable reading of
Cursor's own "same name" wording.
"""

from __future__ import annotations

import itertools
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers import claude_command_agent
from tools.parsers.claude_plugin_root import resolve_within


def _discover_subagent_scopes(root: Path) -> dict[Path, dict[str, dict[Path, Path]]]:
    """Map each scope dir (the directory containing `.claude`/`.cursor`) to
    {host_dir_name: {relative_path: absolute_file}}. Pure discovery — walks
    every depth, independent of host selection (Cursor's compatibility read
    means `.claude/agents/` files are part of Cursor's surface too).

    An `agents_dir` match that is itself a symlink escaping `root` (or has a
    symlinked ancestor, e.g. `.claude` itself) is dropped via `resolve_within`
    — the same containment `claude_plugin_root` applies to a plugin's default
    `agents/` dir, and `iter_unignored_files` gets for free from `os.walk`'s
    `followlinks=False` default used by every other repo-mode manifest walk.
    """
    scopes: dict[Path, dict[str, dict[Path, Path]]] = {}
    for host_dir in (".claude", ".cursor"):
        for agents_dir in sorted(root.glob(f"**/{host_dir}/agents")):
            try:
                rel = agents_dir.relative_to(root)
            except ValueError:
                continue
            if resolve_within(root, str(rel)) is None:
                continue
            files = _agent_files(agents_dir)
            if files:
                scope = agents_dir.parent.parent
                scopes.setdefault(scope, {})[host_dir] = files
    return scopes


def _safe_parse_file(path: Path, *, runtime_hosts: list[str] | None = None) -> list[ComponentRef]:
    """Run `claude_command_agent.parse_file`, swallowing per-file failures.

    Mirrors `graph_build._safe_parse`'s per-manifest isolation: one malformed
    subagent (bad frontmatter, or an inline `mcpServers`/`hooks` block that
    raises inside the child-ref parsers it calls into) must not abort
    discovery of every other subagent in the scan.
    """
    try:
        if runtime_hosts is not None:
            return claude_command_agent.parse_file(path, kind="agent", runtime_hosts=runtime_hosts)
        return claude_command_agent.parse_file(path, kind="agent")
    except Exception:
        return []


def _occurrences_for_scope(
    claude_files: dict[Path, Path],
    cursor_files: dict[Path, Path],
    hosts: list[str],
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    claude_selected = "claude-code" in hosts
    cursor_selected = "cursor" in hosts

    for rel, claude_path in claude_files.items():
        # Override splitting only applies when Cursor is selected: with
        # Cursor unselected, a sibling .cursor/agents file must not perturb
        # the legacy Claude-only output produced below.
        override_exists = rel in cursor_files and cursor_selected
        if override_exists:
            if claude_selected:
                refs.extend(_safe_parse_file(claude_path, runtime_hosts=["claude-code"]))
        elif claude_selected and cursor_selected:
            refs.extend(_safe_parse_file(claude_path, runtime_hosts=["claude-code", "cursor"]))
        elif cursor_selected:
            refs.extend(_safe_parse_file(claude_path, runtime_hosts=["cursor"]))
        elif claude_selected:
            refs.extend(_safe_parse_file(claude_path))

    if cursor_selected:
        for cursor_path in cursor_files.values():
            refs.extend(_safe_parse_file(cursor_path, runtime_hosts=["cursor"]))

    return refs


def resolve_subagent_occurrences(root: Path, hosts: list[str]) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    scopes = _discover_subagent_scopes(root)
    for scope in sorted(scopes):
        host_files = scopes[scope]
        refs.extend(
            _occurrences_for_scope(
                host_files.get(".claude", {}), host_files.get(".cursor", {}), hosts
            )
        )
    return refs


def _agent_files(agents_dir: Path | None) -> dict[Path, Path]:
    if agents_dir is None or not agents_dir.is_dir():
        return {}
    try:
        agents_dir_resolved = agents_dir.resolve()
    except (OSError, RuntimeError):
        return {}
    files: dict[Path, Path] = {}
    for md in sorted(agents_dir.rglob("*.md")):
        if not md.is_file():
            continue
        try:
            md_resolved = md.resolve()
        except (OSError, RuntimeError):
            continue
        # A nested *.md that is itself a symlink (or under a symlinked
        # subdir) escaping agents_dir must not be attributed as a subagent
        # occurrence here — same containment claude_command_agent.enumerate_dir
        # applies via its `contain_within` param for plugin-bundled agents/.
        if not md_resolved.is_relative_to(agents_dir_resolved):
            continue
        files[md.relative_to(agents_dir)] = md
    return files


def resolve_subagent_occurrences_for_dirs(
    claude_agents_dir: Path | None,
    cursor_agents_dir: Path | None,
    hosts: list[str],
) -> list[ComponentRef]:
    """One scope, explicitly-named agents dirs — for callers (endpoint mode)
    whose config roots are arbitrary paths the dot-directory walk can't find."""
    return _occurrences_for_scope(
        _agent_files(claude_agents_dir), _agent_files(cursor_agents_dir), hosts
    )


def group_occurrences_by_manifest(
    refs: list[ComponentRef],
) -> list[tuple[Path, list[ComponentRef]]]:
    """Regroup a flat occurrence list back into (manifest, refs) tuples, for
    callers (`parse_repo_grouped`'s accounting, `descend()`'s graph
    placement) that need per-file grouping rather than a flat list.

    Safe without sorting: `resolve_subagent_occurrences` emits one
    contiguous run of refs per source file — one `parse_file` call per
    matched path — and no two files share a `source_manifest`.
    """
    groups: list[tuple[Path, list[ComponentRef]]] = []
    for manifest, refs_iter in itertools.groupby(refs, key=lambda r: r.source_manifest):
        groups.append((Path(manifest), list(refs_iter)))
    return groups
