"""Resolve an MCP server's launch target to a local dependency-manifest dir.

ADR-0039: `mcp_server` is discovered from a launch declaration (`command`/`args`
or `url`), not a directory, so its dependency supply chain is never attributed.
This module resolves a launch target to the directory whose dep manifest is that
server's supply chain, so `graph_build` can attach the deps as `package` children.

Phase 1 strategies (on-disk package-manager cache resolution is Phase 2):

- **npx/uvx named package** → the dir of a local manifest whose `name` matches the
  launched package (the repo *is* the package, e.g. DesktopCommander). External
  packages (no local manifest) resolve to `None` — their closure is Phase 2.
- **local path** (`node ./dist/server.js`) → the nearest dep manifest at/above the
  referenced on-disk path, within the scan root.
- **remote `url`** → `None` (nothing executes locally).
- `python -m <module>` and other module-form launches → `None` (not a path;
  documented Phase-1 limitation).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools import identity

# Dep-manifest filenames the nearest-manifest walk recognizes (mirrors
# graph_build._DEP_MANIFEST_PARSERS keys).
_DEP_MANIFEST_FILENAMES = (
    "package.json",
    "package-lock.json",
    "bun.lock",
    "pyproject.toml",
    "uv.lock",
)


def strip_launch_version(spec: str) -> str:
    """Strip a trailing `@version` from an npm/PyPI launch spec.

    Handles npm scopes: `@scope/name@1.0.0` → `@scope/name`, `@scope/name` →
    unchanged, `name@latest` → `name`, `name` → unchanged.
    """
    if spec.startswith("@"):
        rest = spec[1:]
        idx = rest.find("@")
        return f"@{rest[:idx]}" if idx != -1 else spec
    idx = spec.find("@")
    return spec[:idx] if idx != -1 else spec


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _nearest_dep_manifest_dir(start: Path, scan_root: Path) -> Path | None:
    """Walk up from `start` to `scan_root` (inclusive) returning the first dir
    that contains a dep manifest, or `None`. Never escapes `scan_root`."""
    try:
        start = start.resolve()
        root = scan_root.resolve()
    except OSError:
        return None
    cur = start if start.is_dir() else start.parent
    if not _within(cur, root):
        return None
    while True:
        if any((cur / fn).is_file() for fn in _DEP_MANIFEST_FILENAMES):
            return cur
        if cur == root or cur.parent == cur:
            return None
        cur = cur.parent


def resolve_mcp_launch_dir(
    ref: Any, *, scan_root: Path, name_index: dict[str, Path]
) -> Path | None:
    """Resolve an MCP server ref's launch target to a dependency-manifest dir.

    `name_index` maps a local manifest `name` → its directory (see
    `graph_build.build_manifest_name_index`). Returns a directory at/under
    `scan_root`, or `None` when the target is remote, external, or unresolvable.
    """
    install_source = (ref.extra or {}).get("install_source")
    if not isinstance(install_source, str) or not install_source.strip():
        return None
    src = install_source.strip()

    # Remote: nothing executes locally.
    if src.startswith(("http://", "https://")):
        return None

    tokens = src.split()

    # Strategy 1: npx/uvx named package → local manifest of the same name.
    # Normalize a full-path launcher (`/usr/local/bin/npx`, `/usr/local/bin/uv`)
    # to its basename so `mcp_package_source` recognizes it — it matches the
    # launcher token exactly, and the basename preserves the `uv tool run`
    # dispatch in `launcher_and_args`.
    normalized = " ".join([Path(tokens[0]).name, *tokens[1:]]) if tokens else src
    pkg_source = identity.mcp_package_source(normalized)
    if pkg_source is not None:
        _launcher, _ecosystem, package = pkg_source
        return name_index.get(strip_launch_version(package))

    # Strategy 2: a local path argument → nearest dep manifest at/above it.
    # Anchor relative launch paths at the right directory: for an MCP declared
    # inline in `.claude-plugin/plugin.json`, `source_manifest` is the plugin.json,
    # so the path is relative to the plugin ROOT (one level above `.claude-plugin/`),
    # not the manifest's own directory.
    manifest_dir = scan_root
    src_path = Path(ref.source_manifest) if ref.source_manifest else None
    if src_path is not None:
        manifest_dir = src_path.parent
        if src_path.name == "plugin.json" and src_path.parent.name == ".claude-plugin":
            manifest_dir = src_path.parent.parent
    for tok in tokens[1:]:
        if tok.startswith("-"):
            continue
        # Only treat tokens that look like filesystem paths as candidates; a
        # bare module name (`aiteam.mcp.server`) or subcommand is not a path.
        if "/" not in tok and not tok.startswith("."):
            continue
        candidate = Path(tok) if tok.startswith("/") else (manifest_dir / tok)
        try:
            if candidate.exists():
                return _nearest_dep_manifest_dir(candidate, scan_root)
        except OSError:
            continue
    return None
