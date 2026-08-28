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
  and not CI-relevant); `endpoint` includes it.

- `by_scope()` returns each scope's settings preserved unmerged. Used by
  parsers that need scope-of-origin for identity (notably hooks, where
  `claude-hook/settings/<scope>/<event>/<index>` would otherwise lose scope
  provenance after merge).

Managed scope is system-wide policy an administrator distributes: a
`managed-settings.json` plus a `managed-settings.d/*.json` drop-in directory,
under a platform-specific root. It is loaded, because it can declare
`mcpServers` and `hooks` — components — and a scan that skipped it would
report an MDM-managed endpoint's composition as complete while missing them.
"""

from __future__ import annotations

import copy
import json
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from tools.graph import record_gap

Scope = Literal["managed", "local", "project", "user"]
Mode = Literal["repo", "endpoint"]

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


def load(
    install_root: Path,
    project_root: Optional[Path] = None,
    *,
    warnings: list[str] | None = None,
    managed_dir: Optional[Path] = None,
) -> SettingsLayers:
    """Read settings files from disk.

    `install_root` is typically `~/.claude` (the user-scope home). When
    `project_root` is given, also read its `.claude/settings.json` (project
    scope) and `.claude/settings.local.json` (local scope).

    Files that don't exist are skipped. Malformed, unreadable, and non-object
    settings are skipped; callers that need a complete inventory can pass a
    warnings list and decide whether to continue.
    """
    layers = SettingsLayers()
    user_file = install_root / "settings.json"
    if (parsed := _load_object(user_file, warnings)) is not None:
        layers.user = parsed

    if project_root is not None:
        project_file = project_root / ".claude" / "settings.json"
        if (parsed := _load_object(project_file, warnings)) is not None:
            layers.project = parsed
        local_file = project_root / ".claude" / "settings.local.json"
        if (parsed := _load_object(local_file, warnings)) is not None:
            layers.local = parsed

    if (parsed := load_managed(managed_dir, warnings=warnings)) is not None:
        layers.managed = parsed
    return layers


def default_managed_dir() -> Path:
    """Where an administrator's Claude Code policy lands, per platform.

    Matches `tools/policy_cli.py`'s own `_default_managed_settings_dir` — that
    is where OpenACA *writes* policy, so reading anywhere else would mean the
    tool could not see settings it had just installed.
    """
    system = platform.system()
    if system == "Darwin":
        return Path("/Library/Application Support/ClaudeCode")
    if system == "Windows":
        return Path("C:/Program Files/ClaudeCode")
    return Path("/etc/claude-code")


def load_managed(
    directory: Path | None = None, *, warnings: list[str] | None = None
) -> dict | None:
    """Merge `managed-settings.json` and every `managed-settings.d/*.json`.

    Drop-ins are applied in sorted filename order after the base file, which is
    the convention the `NN-name.json` naming implies and the order
    `policy_cli`'s own collision check walks.
    """
    root = directory if directory is not None else default_managed_dir()
    merged: dict = {}
    found = False
    base = root / "managed-settings.json"
    if (parsed := _load_object(base, warnings)) is not None:
        _deep_merge(merged, parsed)
        found = True
    dropins = root / "managed-settings.d"
    if dropins.is_dir():
        try:
            entries = sorted(dropins.glob("*.json"))
        except OSError:
            entries = []
        for path in entries:
            if (parsed := _load_object(path, warnings)) is not None:
                _deep_merge(merged, parsed)
                found = True
    return merged if found else None


def _load_object(path: Path, warnings: list[str] | None) -> dict | None:
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        if warnings is not None:
            record_gap(warnings, f"could not parse settings file {path}: {exc}")
        return None
    if not isinstance(parsed, dict):
        if warnings is not None:
            record_gap(warnings, f"settings file {path} must contain an object")
        return None
    return parsed
