"""Parse Claude Code hooks — both plugin-bundled and settings-scoped.

Two input shapes wrap the same inner format:

- **Plugin format** at `<plugin-root>/hooks/hooks.json`:
  `{"description": "...", "hooks": {<EventName>: [<entry>, ...]}}`
- **Settings format** inside a `settings.json` (any scope):
  `{<EventName>: [<entry>, ...]}` (the value of the `hooks` key)

Each entry is `{"type": "command"|"prompt", "command": "...", "matcher": "..."?}`.

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
    hooks_json_path: Path, plugin_name: str, attributed_to: str
) -> list[ComponentRef]:
    """Walk a plugin's `hooks/hooks.json` file.

    Returns [] for any read/parse error or shape violation (non-object root,
    missing or non-object `hooks` key). Identity uses the plugin name.
    """
    try:
        raw = hooks_json_path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    hooks_block = data.get("hooks")
    if not isinstance(hooks_block, dict):
        return []
    return _walk_events(
        hooks_block,
        source_manifest=str(hooks_json_path),
        attributed_to=attributed_to,
        scope=None,
    )


def parse_plugin_hooks_inline(
    hooks_block: dict, plugin_name: str, source_manifest: str, attributed_to: str
) -> list[ComponentRef]:
    """Walk a plugin.json's inline `hooks` key.

    Same inner shape as hooks/hooks.json (`{event: [entry, ...]}`), but read
    from the plugin.json manifest rather than a separate file. Both sources
    can coexist on the same plugin — no deduplication is applied.
    """
    if not isinstance(hooks_block, dict):
        return []
    return _walk_events(
        hooks_block,
        source_manifest=source_manifest,
        attributed_to=attributed_to,
        scope=None,
    )


def parse_settings_hooks(
    settings_path: Path, hooks_block: object, scope: str
) -> list[ComponentRef]:
    """Walk a settings.json's `hooks` block for a specific scope.

    Settings hooks are NOT attributed to any plugin — they're declared
    directly by the user/project/local config. The scope is observation
    metadata, not part of the logical component identity.
    """
    if not isinstance(hooks_block, dict):
        return []
    return _walk_events(
        hooks_block,
        source_manifest=str(settings_path),
        attributed_to=None,
        scope=scope,
    )


def _walk_events(
    hooks_block: dict,
    source_manifest: str,
    attributed_to: Optional[str],
    scope: Optional[str],
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for event, entries in hooks_block.items():
        if not isinstance(event, str) or not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            extra = {
                "event": event,
                "index": index,
                "type": entry.get("type"),
                "command": entry.get("command"),
                "matcher": entry.get("matcher"),
            }
            if scope is not None:
                extra["scope"] = scope
            extra["component_type"] = "hook"
            refs.append(
                ComponentRef(
                    component_identity=_hook_identity(entry),
                    source_manifest=source_manifest,
                    source_locator=f"$.hooks.{event}[{index}]",
                    attributed_to=attributed_to,
                    extra=extra,
                )
            )
    return refs


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
