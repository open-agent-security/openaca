"""Disambiguates manifest filenames that collide across hosts (ADR-0044).

Cursor's `mcp.json` and Claude Code's `mcp.json` share a basename; only
directory context tells them apart. This module is the one place that
decides ownership for such filenames, imported by the parser registry
(tools/parsers/__init__.py), graph construction
(tools/graph_build.py), and posture manifest collection (tools/scan.py)
alike — those three call sites independently discover the same files
and must agree on which host owns each one.
"""

from __future__ import annotations

from pathlib import Path


def owning_host(path: Path) -> str:
    """Which registered host's directory convention `path` belongs to.

    Path-based, not content-based: manifest content shape can't
    disambiguate Claude Code's `mcp.json` from Cursor's — both use the
    same `mcpServers` JSON shape. Matches the *exact* shape
    `_is_cursor_mcp_json` recognizes — parent directory named `.cursor`,
    filename exactly `mcp.json` — not merely "somewhere under a
    `.cursor/` directory": `.cursor/.mcp.json` and
    `.cursor/cache/mcp.json` are neither the real Cursor convention nor
    invisible to Claude's catch-all, so both fall back to Claude Code's
    original unqualified convention (bare `mcp.json`/`.mcp.json`
    anywhere in the tree, predating any other host) — the same
    treatment they'd get if Cursor didn't exist at all.
    """
    if len(path.parts) >= 2 and path.parts[-2:] == (".cursor", "mcp.json"):
        return "cursor"
    return "claude-code"


def resolved_owner(path: Path, manifest_hosts: dict[Path, str] | None) -> str:
    """`owning_host(path)`, overridden by collection provenance when given.

    `manifest_hosts` (built by `collect_endpoint_posture_inputs`) records
    which host's collector actually produced each manifest — the only
    correct classification for an explicit `--config-dir` root not named
    after the host's own directory convention (`owning_host` is path-shape
    based and would misattribute it). Falls back to `owning_host` for a
    manifest the map doesn't cover — e.g. a Claude settings manifest, which
    `collect_endpoint_posture_inputs` never adds to the map because it is
    inherently Claude-owned.
    """
    if manifest_hosts is not None:
        mapped = manifest_hosts.get(path)
        if mapped is not None:
            return mapped
    return owning_host(path)
