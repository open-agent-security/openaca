"""Manifest parser registry."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from tools.component_ref import ComponentRef
from tools.parsers import (
    claude_plugin,
    claude_settings,
    mcp_json,
    package_json,
    pyproject_toml,
)

ParserFn = Callable[[Path], list[ComponentRef]]

REGISTRY: list[tuple[str, ParserFn]] = [
    ("package.json", package_json.parse),
    ("pyproject.toml", pyproject_toml.parse),
    ("mcp.json", mcp_json.parse),
    (".mcp.json", mcp_json.parse),
    # Claude Desktop user-config: same JSON shape as `mcp.json`
    # (`mcpServers` map of stdio launches), different filename. Reuse
    # the same parser; the filename pattern is the only addition.
    ("claude_desktop_config.json", mcp_json.parse),
    (".claude-plugin/plugin.json", claude_plugin.parse),
    (".claude/settings.json", claude_settings.parse),
]


def parse_repo_grouped(root: Path) -> list[tuple[Path, list[ComponentRef]]]:
    """Walk `root` and return per-manifest results, preserving file boundaries.

    Used by callers that need to report which manifests were scanned (e.g.,
    `asve-scan --verbose`). A single malformed manifest must not abort the
    whole scan — these parsers operate on arbitrary user repos, and one bad
    file should drop only that file, not every other finding. Per-path
    failures are silently dropped. Manifests with zero components (e.g., an
    empty `dependencies` block) are still included so consumers can see the
    file was visited.
    """
    grouped: list[tuple[Path, list[ComponentRef]]] = []
    for pattern, parser in REGISTRY:
        for path in root.rglob(pattern):
            try:
                grouped.append((path, parser(path)))
            except Exception:
                continue
    return grouped


def parse_repo(root: Path) -> list[ComponentRef]:
    """Walk `root` and return ComponentRefs from all known manifests."""
    refs: list[ComponentRef] = []
    for _, group in parse_repo_grouped(root):
        refs.extend(group)
    return refs
