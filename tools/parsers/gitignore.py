"""`.gitignore` filtering for repo-mode manifest discovery.

Repo scans typically run against a developer's working tree, which often
contains `node_modules/`, `.venv/`, `dist/`, and other build artifacts
that produce noisy or wrong findings when treated as user-declared
manifests (e.g., a vendored `package.json` deep inside `node_modules/`
emits transitive deps as if the host repo declared them).

V0 strategy: parse only `<root>/.gitignore`. Nested `.gitignore` files
(`<root>/subdir/.gitignore`) are common in monorepos but uncommon in
typical agent-component repos; supporting them complicates anchoring rules
without a strong V0 motivation. Add later if a real repo needs it.

`.git/` is always skipped regardless of `.gitignore` content — git
never lists its own metadata directory in `.gitignore` (it's tracked
out-of-band), but walking into it would surface nothing useful and
risk false positives from packed-object filenames or refs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from pathspec import GitIgnoreSpec

_ALWAYS_SKIP_DIRS = (".git",)


def load_gitignore_spec(root: Path) -> Optional[GitIgnoreSpec]:
    """Read `<root>/.gitignore` and return a compiled spec, or None if absent."""
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    try:
        lines = gitignore_path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return GitIgnoreSpec.from_lines(lines)


def is_ignored(rel_path: Path, spec: Optional[GitIgnoreSpec]) -> bool:
    """Return True if `rel_path` (relative to scan root) should be skipped.

    Always returns True for paths under `.git/`. Otherwise consults
    `spec` if present; returns False when `spec` is None (no
    `.gitignore` at scan root = walk everything).
    """
    parts = rel_path.parts
    if parts and parts[0] in _ALWAYS_SKIP_DIRS:
        return True
    if spec is None:
        return False
    # pathspec's gitignore matcher operates on forward-slash strings.
    return spec.match_file(rel_path.as_posix())


def _has_negated_patterns(spec: Optional[GitIgnoreSpec]) -> bool:
    if spec is None:
        return False
    return any(getattr(pattern, "include", None) is False for pattern in spec.patterns)


def _is_ignored_dir(rel_path: Path, spec: Optional[GitIgnoreSpec]) -> bool:
    if rel_path.parts and rel_path.parts[0] in _ALWAYS_SKIP_DIRS:
        return True
    if spec is None:
        return False
    if _has_negated_patterns(spec):
        return False
    rel_posix = rel_path.as_posix().rstrip("/") + "/"
    return spec.match_file(rel_posix)


def iter_unignored_files(root: Path, spec: Optional[GitIgnoreSpec]) -> Iterator[Path]:
    """Yield files under `root`, pruning ignored directories before descent."""
    for dirpath_str, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath_str)
        dirnames.sort()
        kept_dirnames: list[str] = []
        for dirname in dirnames:
            child = dirpath / dirname
            try:
                rel = child.relative_to(root)
            except ValueError:
                rel = child
            if _is_ignored_dir(rel, spec):
                continue
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames

        for filename in sorted(filenames):
            path = dirpath / filename
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            if is_ignored(rel, spec):
                continue
            yield path
