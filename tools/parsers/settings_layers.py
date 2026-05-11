"""Four-scope Claude Code settings reader.

Per Claude Code docs, settings layer in this precedence (narrowest wins):

    Managed
      > Local (settings.local.json)
      > Project (.claude/settings.json)
      > User (~/.claude/settings.json)

Merge rules:

- Arrays union+dedupe (e.g., `permissions.allow`).
- Objects deep-merge with more-specific scope winning per-key
  (e.g., project's `enabledPlugins.foo: false` overrides user's `enabledPlugins.foo: true`).
- Scalars: more-specific scope replaces.

This module exposes two views, picked by the caller based on identity needs:

- `merged(mode)` returns a single effective dict. Used for things where
  merging makes sense (the active enabledPlugins set, scalar feature flags).
  Mode-specific: `repo` skips Local (`settings.local.json` is machine-local
  and not CI-relevant); `fs` includes it.

- `by_scope()` returns each scope's settings preserved unmerged. Used by
  parsers that need scope-of-origin for identity (notably hooks, where
  `claude-hook/settings/<scope>/<event>/<index>` would otherwise lose scope
  provenance after merge).

Managed scope (system-wide policy via plist/registry/managed-settings.json)
is platform-specific and not loaded in V0; the field is reserved.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

Scope = Literal["managed", "local", "project", "user"]
Mode = Literal["repo", "fs"]

# Highest-precedence first.
SCOPE_PRECEDENCE: list[Scope] = ["managed", "local", "project", "user"]


@dataclass
class SettingsLayers:
    user: dict = field(default_factory=dict)
    project: Optional[dict] = None
    local: Optional[dict] = None
    managed: Optional[dict] = None

    def by_scope(self) -> dict[Scope, dict]:
        return {
            "managed": self.managed or {},
            "local": self.local or {},
            "project": self.project or {},
            "user": self.user or {},
        }

    def merged(self, mode: Mode) -> dict:
        # Apply scopes lowest-precedence first; higher-precedence scopes
        # override key-by-key as we walk back up.
        scopes_low_to_high: list[Scope] = list(reversed(SCOPE_PRECEDENCE))
        if mode == "repo":
            scopes_low_to_high = [s for s in scopes_low_to_high if s != "local"]
        result: dict = {}
        per_scope = self.by_scope()
        for scope in scopes_low_to_high:
            # Deep-copy the scope data before merging so nested dicts/lists
            # don't alias into `result`. Without this, a later scope's
            # `_deep_merge` would mutate the prior scope's stored value in
            # place, corrupting `by_scope()` provenance after a `merged()`
            # call. The copy is per-call; layer fields stay untouched.
            data = copy.deepcopy(per_scope[scope] or {})
            _deep_merge(result, data)
        return result


def _deep_merge(target: dict, source: dict) -> None:
    """Mutate `target` by merging `source` into it.

    - Arrays at the same key: union, preserving first-seen order, deduplicating
      by `repr` (covers scalar items and dict items uniformly for the
      conservative cases V0 needs).
    - Dicts at the same key: recursive deep-merge.
    - Otherwise: source replaces target.
    """
    for key, value in source.items():
        if key in target:
            existing = target[key]
            if isinstance(existing, list) and isinstance(value, list):
                seen: set[str] = set()
                merged: list = []
                for item in existing + value:
                    marker = repr(item)
                    if marker in seen:
                        continue
                    seen.add(marker)
                    merged.append(item)
                target[key] = merged
                continue
            if isinstance(existing, dict) and isinstance(value, dict):
                _deep_merge(existing, value)
                continue
        target[key] = value


def load(install_root: Path, project_root: Optional[Path] = None) -> SettingsLayers:
    """Read settings files from disk.

    `install_root` is typically `~/.claude` (the user-scope home). When
    `project_root` is given, also read its `.claude/settings.json` (project
    scope) and `.claude/settings.local.json` (local scope).

    Files that don't exist, fail JSON parsing, or contain a non-object top
    level are silently skipped — we'd rather scan with partial settings than
    abort the whole resolver on one malformed file.
    """
    layers = SettingsLayers()
    user_file = install_root / "settings.json"
    if user_file.exists():
        try:
            parsed = json.loads(user_file.read_text())
            if isinstance(parsed, dict):
                layers.user = parsed
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # JSON syntax errors, read errors (PermissionError,
            # IsADirectoryError, etc.), and decode errors (non-UTF-8 bytes)
            # all fall through silently. Per module docstring: scan with
            # partial settings rather than abort on one bad file.
            pass

    if project_root is not None:
        project_file = project_root / ".claude" / "settings.json"
        if project_file.exists():
            try:
                parsed = json.loads(project_file.read_text())
                if isinstance(parsed, dict):
                    layers.project = parsed
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass
        local_file = project_root / ".claude" / "settings.local.json"
        if local_file.exists():
            try:
                parsed = json.loads(local_file.read_text())
                if isinstance(parsed, dict):
                    layers.local = parsed
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                pass

    # Managed scope (system policy) — not loaded in V0.
    return layers
