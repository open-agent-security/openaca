"""Parse Claude Code hooks — both plugin-bundled and settings-scoped.

Two input shapes wrap the same inner format:

- **Plugin format** at `<plugin-root>/hooks/hooks.json`:
  `{"description": "...", "hooks": {<EventName>: [<entry>, ...]}}`
- **Settings format** inside a `settings.json` (any scope):
  `{<EventName>: [<entry>, ...]}` (the value of the `hooks` key)

Each array entry is a matcher group, `{"matcher": "..."?, "hooks": [<handler>, ...]}`;
a handler is `{"type": "command"|"prompt", "command": "...", ...}`. A group with
no `hooks` array is treated as a single handler in place (see `_walk_events`).

Identity is derived from the hook payload, not where the hook is declared.
`event`, array `index`, settings `scope`, `type`, `command`, and `matcher`
live in `extra`; `source_manifest` and `source_locator` carry the observed
location.

Skipped silently on read or parse errors so one malformed hooks block
doesn't abort the wider scan.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from tools.component_ref import ComponentRef


def parse_plugin_hooks(
    hooks_json_path: Path, plugin_name: str, *, strict: bool = False
) -> list[ComponentRef]:
    """Walk a plugin's `hooks/hooks.json` file.

    Returns [] for any read/parse error or shape violation (non-object root,
    missing or non-object `hooks` key). Identity uses the plugin name.
    """
    try:
        raw = hooks_json_path.read_text()
    except (OSError, UnicodeDecodeError):
        if strict:
            raise
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if strict:
            raise
        return []
    if not isinstance(data, dict):
        if strict:
            raise ValueError("plugin hooks file must contain an object")
        return []
    hooks_block = data.get("hooks")
    if not isinstance(hooks_block, dict):
        if strict:
            raise ValueError("plugin hooks file hooks must be an object")
        return []
    if strict:
        _validate_hook_events(hooks_block, "plugin hook")
    return _walk_events(
        hooks_block,
        source_manifest=str(hooks_json_path),
        scope=None,
    )


def parse_standalone_hooks(
    hooks_json_path: Path, *, scope: Optional[str] = None, strict: bool = False
) -> list[ComponentRef]:
    """Walk a standalone `hooks.json` — a hooks file that is neither bundled in
    a plugin nor embedded in a settings file.

    Codex declares project hooks this way (`<project>/.codex/hooks.json`).
    The envelope and event vocabulary are Claude Code's, so only the entry
    point is new; `scope` is carried onto each ref the same way
    `parse_settings_hooks` carries it.
    """
    try:
        raw = hooks_json_path.read_text()
    except (OSError, UnicodeDecodeError):
        if strict:
            raise
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if strict:
            raise
        return []
    if not isinstance(data, dict):
        if strict:
            raise ValueError("hooks file must contain an object")
        return []
    hooks_block = data.get("hooks")
    if not isinstance(hooks_block, dict):
        if strict:
            raise ValueError("hooks file hooks must be an object")
        return []
    if strict:
        _validate_hook_events(hooks_block, "hook")
    return _walk_events(hooks_block, source_manifest=str(hooks_json_path), scope=scope)


def parse_plugin_hooks_inline(
    hooks_block: dict, plugin_name: str, source_manifest: str, *, strict: bool = False
) -> list[ComponentRef]:
    """Walk a plugin.json's inline `hooks` key.

    Same inner shape as hooks/hooks.json (`{event: [entry, ...]}`), but read
    from the plugin.json manifest rather than a separate file. Both sources
    can coexist on the same plugin — no deduplication is applied.
    """
    if not isinstance(hooks_block, dict):
        if strict:
            raise ValueError("plugin hooks must be an object")
        return []
    if strict:
        _validate_hook_events(hooks_block, "plugin hook")
    return _walk_events(
        hooks_block,
        source_manifest=source_manifest,
        scope=None,
    )


def parse_settings_hooks(
    settings_path: Path, hooks_block: object, scope: str, *, strict: bool = False
) -> list[ComponentRef]:
    """Walk a settings.json's `hooks` block for a specific scope.

    Settings hooks are declared directly by the user/project/local config.
    The scope is observation metadata, not part of the logical component
    identity; parentage is set by the graph edge.
    """
    if not isinstance(hooks_block, dict):
        if strict:
            raise ValueError("settings hooks must be an object")
        return []
    if strict:
        _validate_hook_events(hooks_block, "settings hook")
    return _walk_events(
        hooks_block,
        source_manifest=str(settings_path),
        scope=scope,
    )


def _validate_hook_events(hooks_block: dict, label: str) -> None:
    """Mirrors `_walk_events`'s own descent: a malformed handler nested inside
    a matcher group's `hooks` array is exactly as invalid as a malformed
    top-level entry, and strict mode must raise on both so a caller's
    `_safe_parse`/`record_gap` sees the drop rather than composing silently
    with one fewer hook than the file actually declares.
    """
    for event, entries in hooks_block.items():
        if not isinstance(event, str) or not isinstance(entries, list):
            raise ValueError(f"{label} events must map strings to arrays")
        for group in entries:
            if not isinstance(group, dict):
                raise ValueError(f"{label} event entries must be objects")
            handlers = group.get("hooks")
            if handlers is None:
                continue
            if not isinstance(handlers, list):
                raise ValueError(f"{label} nested hook handlers must be an array")
            if any(not isinstance(entry, dict) for entry in handlers):
                raise ValueError(f"{label} nested hook handlers must be objects")


def _walk_events(
    hooks_block: dict,
    source_manifest: str,
    scope: Optional[str],
) -> list[ComponentRef]:
    """Each event's array holds matcher groups, not handlers directly:
    `{"matcher": "...", "hooks": [<handler>, ...]}` (verified against
    code.claude.com/docs/en/hooks). The handler objects are one level down,
    inside the group's own `hooks` array.

    A group with no `hooks` list is degenerate input rather than a second
    format — it is treated as a single handler in place, so a malformed or
    hand-written flat entry still yields one hook rather than silently none.
    """
    refs: list[ComponentRef] = []
    for event, entries in hooks_block.items():
        if not isinstance(event, str) or not isinstance(entries, list):
            continue
        for group_index, group in enumerate(entries):
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            handlers = group.get("hooks")
            if isinstance(handlers, list):
                for index, entry in enumerate(handlers):
                    if not isinstance(entry, dict):
                        continue
                    refs.append(
                        _hook_ref(
                            entry,
                            event=event,
                            index=index,
                            matcher=matcher,
                            scope=scope,
                            source_manifest=source_manifest,
                            source_locator=f"$.hooks.{event}[{group_index}].hooks[{index}]",
                        )
                    )
            else:
                refs.append(
                    _hook_ref(
                        group,
                        event=event,
                        index=group_index,
                        matcher=matcher,
                        scope=scope,
                        source_manifest=source_manifest,
                        source_locator=f"$.hooks.{event}[{group_index}]",
                    )
                )
    return refs


def _hook_ref(
    entry: dict,
    *,
    event: str,
    index: int,
    matcher: object,
    scope: Optional[str],
    source_manifest: str,
    source_locator: str,
) -> ComponentRef:
    extra = {
        "event": event,
        "index": index,
        "type": entry.get("type"),
        "command": entry.get("command"),
        "matcher": matcher,
    }
    if scope is not None:
        extra["scope"] = scope
    extra["component_type"] = "hook"
    return ComponentRef(
        component_identity=_hook_identity(entry),
        source_manifest=source_manifest,
        source_locator=source_locator,
        extra=extra,
    )


def _hook_identity(entry: dict) -> str:
    hook_type = entry.get("type")
    command = entry.get("command")
    prompt = entry.get("prompt")
    payload = {
        "type": hook_type if isinstance(hook_type, str) else "",
        "command": command if isinstance(command, str) else "",
        "prompt": prompt if isinstance(prompt, str) else "",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    kind = payload["type"] or "hook"
    return f"claude-hook/{kind}:{digest}"
