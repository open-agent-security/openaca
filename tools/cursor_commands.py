"""Last-wins precedence resolver for Cursor + Claude Code commands.

Commands are keyed by **relative path under their `commands` root**, never by
frontmatter `name`, so `a/deploy.md` and `b/deploy.md` never collide. Given
that, real collisions on the *same* relative path resolve last-wins over an
ordered list of `commands` directories: team → global → plugin → workspace
`.claude` → workspace `.cursor` → personal `.claude` → personal `.cursor`,
each step unconditionally overwriting whatever a same-relative-path entry
from an earlier step resolved to. **User (personal) scope is the eventual
winner** — the last entry seen for a given relative path replaces, it never
merges with, an earlier one.

**This is the opposite of `tools/cursor_subagents.py`, which resolves
first-wins** (there, project/workspace scope beats personal scope entirely).
Do not unify the two resolvers into one shared walker or a
`first_wins`/`reverse` parameter — that is exactly the simplification most
likely to silently invert one of them.

`.codex/agents` is deliberately not a root here: Cursor's docs mention it,
but neither shipped program reads it. (Nor does this module read
`.codex/commands` — Cursor's own command tiers never name it.)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from tools.component_ref import ComponentRef
from tools.parsers import claude_command_agent
from tools.parsers.claude_plugin_root import resolve_within

COMMANDS_DIRNAME = "commands"

# docs/specs/cursor-agent-kind.md "Where each surface loads from": commands
# accept `.md`, `.txt` — NOT `.mdc` (that's subagents, the opposite set; see
# `tools/cursor_subagents.py`).
_COMMAND_EXTENSIONS = (".md", ".txt")

# docs/specs/cursor-agent-kind.md "Where each surface loads from": commands
# traverse "recursive, depth 10" — a file directly under `commands_dir` is
# depth 1, so up to 9 intervening directories are in scope and a 10th is not.
_MAX_TRAVERSAL_DEPTH = 10

# Precedence within a single scope, lowest index resolved first (last-wins
# overwrites it): `.cursor` overwrites `.claude` within the same scope.
SCOPE_DIR_ORDER: tuple[str, ...] = (".claude", ".cursor")


@dataclass(frozen=True)
class ResolvedCommand:
    """One command that survived precedence resolution."""

    relative_path: str
    file_path: Path
    commands_dir: Path
    refs: tuple[ComponentRef, ...]


def resolve_repo(
    repo_root: Path, is_ignored: Optional[Callable[[Path], bool]] = None
) -> list[ResolvedCommand]:
    """Repo mode: discover every `{.claude,.cursor}/commands` dir under
    `repo_root`, grouped by the directory that contains the scope dir
    (`commands_dir.parent.parent`), and resolve each group independently
    with `.cursor` overwriting `.claude`.

    `is_ignored`, when given, is consulted per-candidate-file BEFORE a file
    participates in last-wins resolution (not after, on the winner only): a
    gitignored higher-precedence file must never shadow an unignored
    lower-precedence file at the same relative path — dropping the winner
    post-resolution would silently drop both.
    """
    groups: dict[Path, dict[str, Path]] = {}
    for scope_dirname in SCOPE_DIR_ORDER:
        for commands_dir in sorted(repo_root.rglob(f"{scope_dirname}/{COMMANDS_DIRNAME}")):
            if not commands_dir.is_dir():
                continue
            group_root = commands_dir.parent.parent
            groups.setdefault(group_root, {})[scope_dirname] = commands_dir

    resolved: list[ResolvedCommand] = []
    for group_root in sorted(groups):
        by_dirname = groups[group_root]
        ordered_dirs = [by_dirname[d] for d in SCOPE_DIR_ORDER if d in by_dirname]
        resolved.extend(_resolve_ordered_dirs(repo_root, ordered_dirs, is_ignored=is_ignored))
    return resolved


def resolve_endpoint(scope_dirs: Sequence[Path]) -> list[ResolvedCommand]:
    """Endpoint mode: `scope_dirs` are explicitly named `commands`
    directories, lowest precedence first (e.g. team, global, plugin,
    workspace `.claude`, workspace `.cursor`, personal `.claude`, personal
    `.cursor`). Each later directory overwrites a same-relative-path entry
    from an earlier one.

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
) -> list[ResolvedCommand]:
    resolved: dict[str, ResolvedCommand] = {}
    for commands_dir in ordered_dirs:
        if containment_root is not None:
            try:
                rel = commands_dir.relative_to(containment_root)
            except ValueError:
                continue
            if resolve_within(containment_root, rel.as_posix()) is None:
                continue
        if not commands_dir.is_dir():
            continue
        for relative_path, file_path in _iter_command_files(commands_dir):
            if is_ignored is not None and is_ignored(file_path):
                continue
            resolved[relative_path] = ResolvedCommand(
                relative_path=relative_path,
                file_path=file_path,
                commands_dir=commands_dir,
                refs=tuple(_parse_isolated(file_path)),
            )
    return [resolved[key] for key in sorted(resolved)]


def _iter_command_files(commands_dir: Path) -> list[tuple[str, Path]]:
    try:
        commands_dir_resolved = commands_dir.resolve()
    except (OSError, RuntimeError):
        return []
    out: list[tuple[str, Path]] = []
    for child in sorted(commands_dir.rglob("*")):
        if not child.is_file() or child.suffix not in _COMMAND_EXTENSIONS:
            continue
        relative_path = child.relative_to(commands_dir)
        if len(relative_path.parts) > _MAX_TRAVERSAL_DEPTH:
            continue
        try:
            child_resolved = child.resolve()
        except (OSError, RuntimeError):
            continue
        if not child_resolved.is_relative_to(commands_dir_resolved):
            continue
        out.append((relative_path.as_posix(), child))
    return out


def _parse_isolated(file_path: Path) -> list[ComponentRef]:
    try:
        return claude_command_agent.parse_file(
            file_path, kind="command", extensions=_COMMAND_EXTENSIONS
        )
    except Exception:
        return []
