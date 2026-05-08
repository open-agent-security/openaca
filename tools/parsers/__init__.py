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
    (".claude-plugin/plugin.json", claude_plugin.parse),
    (".claude/settings.json", claude_settings.parse),
]


def parse_repo(root: Path) -> list[ComponentRef]:
    refs: list[ComponentRef] = []
    for pattern, parser in REGISTRY:
        for path in root.rglob(pattern):
            refs.extend(parser(path))
    return refs
