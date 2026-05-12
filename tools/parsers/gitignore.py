"""`.gitignore` filtering for repo-mode manifest discovery.

Repo scans typically run against a developer's working tree, which often
contains `node_modules/`, `.venv/`, `dist/`, and other build artifacts
that produce noisy or wrong findings when treated as user-declared
manifests (e.g., a vendored `package.json` deep inside `node_modules/`
emits transitive deps as if the host repo declared them).

V0 strategy: parse only `<root>/.gitignore`. Nested `.gitignore` files
(`<root>/subdir/.gitignore`) are common in monorepos but uncommon in
typical agent-stack repos; supporting them complicates anchoring rules
without a strong V0 motivation. Add later if a real repo needs it.

`.git/` is always skipped regardless of `.gitignore` content — git
never lists its own metadata directory in `.gitignore` (it's tracked
out-of-band), but walking into it would surface nothing useful and
risk false positives from packed-object filenames or refs.
"""

from __future__ import annotations

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
