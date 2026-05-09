"""Manifest parser registry."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from tools.component_ref import ComponentRef
from tools.parsers import claude_plugin, claude_settings, mcp_json, package_json

ParserFn = Callable[[Path], list[ComponentRef]]

REGISTRY: list[tuple[str, ParserFn]] = [
    ("package.json", package_json.parse),
    ("mcp.json", mcp_json.parse),
    (".mcp.json", mcp_json.parse),
    # Claude Desktop user-config: same JSON shape as `mcp.json`
    # (`mcpServers` map of stdio launches), different filename. Reuse
    # the same parser; the filename pattern is the only addition.
    ("claude_desktop_config.json", mcp_json.parse),
    (".claude-plugin/plugin.json", claude_plugin.parse),
    (".claude/settings.json", claude_settings.parse),
]


def parse_repo(root: Path) -> list[ComponentRef]:
    """Walk `root` and return ComponentRefs from all known manifests.

    A single malformed manifest (e.g., invalid JSON) must not abort the whole
    scan — these parsers operate on arbitrary user repos, and one bad file
    should drop only that file, not every other finding. Per-path failures
    are silenced; we don't surface them in V0 (the reference Action will
    accumulate per-path errors when it lands).
    """
    refs: list[ComponentRef] = []
    for pattern, parser in REGISTRY:
        for path in root.rglob(pattern):
            try:
                refs.extend(parser(path))
            except Exception:
                continue
    return refs
