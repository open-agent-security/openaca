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


def parse_repo_grouped(
    root: Path,
) -> tuple[list[tuple[Path, list[ComponentRef]]], int]:
    """Walk `root` and return (per-manifest results, total paths matched).

    The second element counts every path that matched a registry pattern,
    regardless of whether parsing succeeded. Callers use this to distinguish
    "target had no manifests at all" (n_found == 0) from "target had manifests
    that all failed to parse" (n_found > 0 but grouped is empty).

    Per-path parse failures are silently dropped — these parsers run against
    arbitrary user repos and one malformed file should not abort the rest of
    the scan. Manifests with zero components are still included so consumers
    can see the file was visited.
    """
    grouped: list[tuple[Path, list[ComponentRef]]] = []
    n_found = 0
    for pattern, parser in REGISTRY:
        for path in root.rglob(pattern):
            n_found += 1
            try:
                grouped.append((path, parser(path)))
            except Exception:
                continue
    return grouped, n_found


def parse_repo(root: Path) -> list[ComponentRef]:
    """Walk `root` and return ComponentRefs from all known manifests."""
    refs: list[ComponentRef] = []
    grouped, _ = parse_repo_grouped(root)
    for _, group in grouped:
        refs.extend(group)
    return refs
