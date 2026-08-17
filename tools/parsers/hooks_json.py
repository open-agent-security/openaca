"""Parse plugin-bundled and settings-scoped hooks — Claude Code and Cursor
Plugins share the same wrapper/entry-array shape, but the event-name
vocabulary and the occurrence-local identity label are format-specific.

Two input shapes wrap the same inner format:

- **Plugin format** at `<plugin-root>/hooks/hooks.json`:
  `{"description": "...", "hooks": {<EventName>: [<entry>, ...]}}`
- **Settings format** inside a `settings.json` (any scope, Claude-only):
  `{<EventName>: [<entry>, ...]}` (the value of the `hooks` key)

Each entry is `{"type": "command"|"prompt", "command": "...", "matcher": "..."?}`
for Claude Code. Cursor Plugins use the same `{event: [entry, ...]}` array
shape and the same standalone `{"hooks": {...}}` wrapper, but a disjoint
camelCase event vocabulary (`preToolUse`, `postToolUse`, `postToolUseFailure`,
`beforeSubmitPrompt`, `stop`, plus agent-lifecycle events), and entries carry
`command`/`matcher` only — no `type` field (verified against Cursor's plugin
reference docs, docs/specs/multi-host-support.md Hooks section). The walk
below is permissive about both: any string event name is accepted and
recorded as-is, and absent `type`/`matcher` fields degrade the identity hash
to a command-only digest rather than being rejected.

Identity is derived from the hook payload, not where the hook is declared.
`event`, array `index`, settings `scope`, `type`, `command`, and `matcher`
live in `extra`; `source_manifest` and `source_locator` carry the observed
location. `identity_scheme` (`"claude-hook"` default, `"cursor-hook"` for a
Cursor Plugin bundle) is occurrence-local display metadata, not identity —
`tools/identity.py`'s `canonical_component_identity` routes `hook` refs
through `_plugin_private_identity`, which never reads this string.

Skipped silently on read or parse errors so one malformed hooks block
doesn't abort the wider scan.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from tools.component_ref import ComponentRef


def hook_identity_scheme_for_manifest(plugin_json_path: Path) -> str:
    """`"cursor-hook"` for a `.cursor-plugin/plugin.json` bundle, else
    `"claude-hook"` — same directory-name keying as
    `claude_plugin_root.default_mcp_filename_for_manifest`."""
    if plugin_json_path.parent.name == ".cursor-plugin":
        return "cursor-hook"
    return "claude-hook"


def parse_plugin_hooks(
    hooks_json_path: Path,
    plugin_name: str,
    runtime_hosts: Optional[list[str]] = None,
    identity_scheme: str = "claude-hook",
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
        scope=None,
        runtime_hosts=runtime_hosts,
        identity_scheme=identity_scheme,
    )


def parse_plugin_hooks_inline(
    hooks_block: dict,
    plugin_name: str,
    source_manifest: str,
    runtime_hosts: Optional[list[str]] = None,
    identity_scheme: str = "claude-hook",
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
        scope=None,
        runtime_hosts=runtime_hosts,
        identity_scheme=identity_scheme,
    )


def parse_settings_hooks(
    settings_path: Path, hooks_block: object, scope: str
) -> list[ComponentRef]:
    """Walk a settings.json's `hooks` block for a specific scope.

    Settings hooks are declared directly by the user/project/local config.
    The scope is observation metadata, not part of the logical component
    identity; parentage is set by the graph edge.
    """
    if not isinstance(hooks_block, dict):
        return []
    return _walk_events(
        hooks_block,
        source_manifest=str(settings_path),
        scope=scope,
    )


def _walk_events(
    hooks_block: dict,
    source_manifest: str,
    scope: Optional[str],
    runtime_hosts: Optional[list[str]] = None,
    identity_scheme: str = "claude-hook",
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
            if runtime_hosts is not None:
                extra["runtime_hosts"] = runtime_hosts
            extra["component_type"] = "hook"
            refs.append(
                ComponentRef(
                    component_identity=_hook_identity(entry, identity_scheme),
                    source_manifest=source_manifest,
                    source_locator=f"$.hooks.{event}[{index}]",
                    extra=extra,
                )
            )
    return refs


def _hook_identity(entry: dict, identity_scheme: str = "claude-hook") -> str:
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
    return f"{identity_scheme}/{kind}:{digest}"
