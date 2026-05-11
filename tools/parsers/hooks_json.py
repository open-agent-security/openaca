"""Parse Claude Code hooks — both plugin-bundled and settings-scoped.

Two input shapes wrap the same inner format:

- **Plugin format** at `<plugin-root>/hooks/hooks.json`:
  `{"description": "...", "hooks": {<EventName>: [<entry>, ...]}}`
- **Settings format** inside a `settings.json` (any scope):
  `{<EventName>: [<entry>, ...]}` (the value of the `hooks` key)

Each entry is `{"type": "command"|"prompt", "command": "...", "matcher": "..."?}`.

Identity scopes:

- Plugin-bundled: `claude-hook/<plugin>/<event>/<index>`
- Settings-scoped: `claude-hook/settings/<scope>/<event>/<index>`
  (scope ∈ user, project, local — settings_layers.SCOPE_PRECEDENCE)

`type` / `command` / `matcher` live in `extra` so identity is stable
across config edits at the same slot (the slot is the unit of inventory,
not the command string).

Skipped silently on read or parse errors so one malformed hooks block
doesn't abort the wider scan.
"""

from __future__ import annotations

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
        identity_prefix=f"claude-hook/{plugin_name}",
        source_manifest=str(hooks_json_path),
        attributed_to=attributed_to,
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
        identity_prefix=f"claude-hook/{plugin_name}",
        source_manifest=source_manifest,
        attributed_to=attributed_to,
    )


def parse_settings_hooks(
    settings_path: Path, hooks_block: object, scope: str
) -> list[ComponentRef]:
    """Walk a settings.json's `hooks` block for a specific scope.

    Settings hooks are NOT attributed to any plugin — they're declared
    directly by the user/project/local config. The scope is part of the
    identity so the same logical hook at multiple scopes emits distinct
    components rather than being merged.
    """
    if not isinstance(hooks_block, dict):
        return []
    return _walk_events(
        hooks_block,
        identity_prefix=f"claude-hook/settings/{scope}",
        source_manifest=str(settings_path),
        attributed_to=None,
    )


def _walk_events(
    hooks_block: dict,
    identity_prefix: str,
    source_manifest: str,
    attributed_to: Optional[str],
) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for event, entries in hooks_block.items():
        if not isinstance(event, str) or not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            # Skip malformed entries but preserve the index — identity at
            # slot N must be stable regardless of malformed neighbors.
            if not isinstance(entry, dict):
                continue
            identity = f"{identity_prefix}/{event}/{index}"
            extra = {
                "type": entry.get("type"),
                "command": entry.get("command"),
                "matcher": entry.get("matcher"),
            }
            refs.append(
                ComponentRef(
                    ecosystem="claude-hook",
                    component_identity=identity,
                    source_manifest=source_manifest,
                    source_locator=f"$.hooks.{event}[{index}]",
                    attributed_to=attributed_to,
                    extra=extra,
                )
            )
    return refs
