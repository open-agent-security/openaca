"""First-wins precedence resolver for Cursor + Claude Code subagents.

Subagents are keyed by **relative path under their `agents` root**, never by
frontmatter `name` (which defaults to the filename anyway), so `a/deploy.md`
and `b/deploy.md` never collide. Given that, real collisions on the *same*
relative path resolve first-wins over an ordered list of `agents`
directories: within one scope `.cursor/agents/<rel>` beats
`.claude/agents/<rel>`, and a project (workspace) scope's subagents entirely
displace a personal (user) scope's — directory order applies *within* a
scope, never across scopes. Concretely, a workspace `.claude/agents/foo.md`
beats `~/.cursor/agents/foo.md`, because scope precedence outranks the
`.cursor`-over-`.claude` rule inside a single scope.

**This is the opposite of `tools/cursor_commands.py`, which resolves
last-wins** (a later-scoped file unconditionally overwrites an earlier one,
so *user* scope wins there). Do not unify the two resolvers into one shared
walker or a `first_wins`/`reverse` parameter — that is exactly the
simplification most likely to silently invert one of them.

`.codex/agents` is deliberately not a root here: Cursor's docs mention it,
but neither shipped program reads it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from tools.component_ref import ComponentRef
from tools.parsers import claude_command_agent
from tools.parsers.claude_plugin_root import resolve_within

AGENTS_DIRNAME = "agents"

# docs/specs/cursor-agent-kind.md "Where each surface loads from": subagents
# accept `.md`, `.mdc`, `.markdown` — NOT `.txt` (that's commands, the
# opposite set; see `tools/cursor_commands.py`).
_AGENT_EXTENSIONS = (".md", ".mdc", ".markdown")

# docs/specs/cursor-agent-kind.md "Where each surface loads from": subagents
# traverse "recursive, depth 10" — a file directly under `agents_dir` is
# depth 1, so up to 9 intervening directories are in scope and a 10th is not.
_MAX_TRAVERSAL_DEPTH = 10

# Precedence within a single scope, lowest index wins (first-wins).
SCOPE_DIR_ORDER: tuple[str, ...] = (".cursor", ".claude")


@dataclass(frozen=True)
class ResolvedSubagent:
    """One subagent that survived precedence resolution."""

    relative_path: str
    file_path: Path
    agents_dir: Path
    refs: tuple[ComponentRef, ...]


def resolve_repo(
    repo_root: Path, is_ignored: Optional[Callable[[Path], bool]] = None
) -> list[ResolvedSubagent]:
    """Repo mode: discover every `{.cursor,.claude}/agents` dir under
    `repo_root`, grouped by the directory that contains the scope dir
    (`agents_dir.parent.parent`), and resolve each group independently with
    `.cursor` first-wins over `.claude`.

    `is_ignored`, when given, is consulted per-candidate-file BEFORE a file
    participates in first-wins resolution (not after, on the winner only): a
    gitignored higher-precedence file must never shadow an unignored
    lower-precedence file at the same relative path — dropping the winner
    post-resolution would silently drop both.
    """
    groups: dict[Path, dict[str, Path]] = {}
    for scope_dirname in SCOPE_DIR_ORDER:
        for agents_dir in sorted(repo_root.rglob(f"{scope_dirname}/{AGENTS_DIRNAME}")):
            if not agents_dir.is_dir():
                continue
            group_root = agents_dir.parent.parent
            groups.setdefault(group_root, {})[scope_dirname] = agents_dir

    resolved: list[ResolvedSubagent] = []
    for group_root in sorted(groups):
        by_dirname = groups[group_root]
        ordered_dirs = [by_dirname[d] for d in SCOPE_DIR_ORDER if d in by_dirname]
        resolved.extend(_resolve_ordered_dirs(repo_root, ordered_dirs, is_ignored=is_ignored))
    return resolved


def resolve_endpoint(scope_dirs: Sequence[Path]) -> list[ResolvedSubagent]:
    """Endpoint mode: `scope_dirs` are explicitly named `agents` directories,
    highest precedence first (e.g. workspace `.cursor/agents`, workspace
    `.claude/agents`, personal `.cursor/agents`, personal `.claude/agents`).

    Endpoint roots are arbitrary paths on the scanned machine and are never
    reconstructed from a directory basename — the caller supplies each one
    directly.
    """
    return _resolve_ordered_dirs(None, list(scope_dirs))


def _resolve_ordered_dirs(
    containment_root: Optional[Path],
    ordered_dirs: list[Path],
    *,
    is_ignored: Optional[Callable[[Path], bool]] = None,
) -> list[ResolvedSubagent]:
    seen: dict[str, ResolvedSubagent] = {}
    for agents_dir in ordered_dirs:
        if containment_root is not None:
            try:
                rel = agents_dir.relative_to(containment_root)
            except ValueError:
                continue
            if resolve_within(containment_root, rel.as_posix()) is None:
                continue
        if not agents_dir.is_dir():
            continue
        for relative_path, file_path in _iter_agent_files(agents_dir):
            if relative_path in seen:
                continue
            if is_ignored is not None and is_ignored(file_path):
                continue
            seen[relative_path] = ResolvedSubagent(
                relative_path=relative_path,
                file_path=file_path,
                agents_dir=agents_dir,
                refs=tuple(_parse_isolated(file_path)),
            )
    return [seen[key] for key in sorted(seen)]


def _iter_agent_files(agents_dir: Path) -> list[tuple[str, Path]]:
    try:
        agents_dir_resolved = agents_dir.resolve()
    except (OSError, RuntimeError):
        return []
    out: list[tuple[str, Path]] = []
    for child in sorted(agents_dir.rglob("*")):
        if not child.is_file() or child.suffix not in _AGENT_EXTENSIONS:
            continue
        relative_path = child.relative_to(agents_dir)
        if len(relative_path.parts) > _MAX_TRAVERSAL_DEPTH:
            continue
        try:
            child_resolved = child.resolve()
        except (OSError, RuntimeError):
            continue
        if not child_resolved.is_relative_to(agents_dir_resolved):
            continue
        out.append((relative_path.as_posix(), child))
    return out


def _parse_isolated(file_path: Path) -> list[ComponentRef]:
    try:
        return claude_command_agent.parse_file(
            file_path, kind="agent", extensions=_AGENT_EXTENSIONS
        )
    except Exception:
        return []
