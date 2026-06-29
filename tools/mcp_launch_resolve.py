"""Resolve an MCP server's launch target to a local dependency-manifest dir.

ADR-0039, **Phase 1 — name-match only.** An MCP server is discovered from a launch
declaration (`command`/`args` or `url`), not a directory, so its dependency supply
chain is never attributed. We resolve it in exactly one bounded, high-confidence
case:

> an `npx`/`uvx <pkg>` launch whose package name matches a local manifest `name`
> (the repo *is* the package it launches — e.g. DesktopCommander).

**Everything else resolves to `None`** — remote `url`, external packages not
present locally, and *all* local-path / `node ./x.js` / `python -m` / env-wrapped /
exotic-launcher forms.

Why no launch-string interpretation: parsing arbitrary launch commands to guess
"which token is the entrypoint and where its code lives" is an open-ended input
space (npx/uvx/bunx/node/python -m/env-wrapped/eval flags/quoting/config-vs-
entrypoint). Heuristics over it never converge — every launch shape is a new edge
case, and guessing wrong produces *false* advisories (worse than a miss for a
security tool). Those cases belong to **Phase 2**, which reads the on-disk
package-manager cache (what was *actually* installed) instead of parsing the
launch string. So this module deliberately extracts the package coordinate (via
the shared `identity` helper, the same one advisory matching uses) and does a
dictionary lookup — nothing more.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from tools import identity


def normalize_pypi_name(name: str) -> str:
    """Canonical comparison form for Python package names (PEP 503-style)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def strip_launch_version(spec: str) -> str:
    """Strip a trailing version pin / extras from an npm or PyPI launch spec.

    npm: `@scope/name@1.0.0` → `@scope/name`, `name@latest` → `name`.
    PyPI: `name==1.2.3` → `name`, `name[extra]>=1,<2` → `name`.
    Unversioned specs are returned unchanged.
    """
    if spec.startswith("@"):
        rest = spec[1:]
        idx = rest.find("@")
        return f"@{rest[:idx]}" if idx != -1 else spec
    idx = spec.find("@")
    if idx != -1:
        return spec[:idx]
    m = re.search(r"[(\[;]|[><=!~]", spec)
    return spec[: m.start()] if m else spec


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve_mcp_launch_dir(
    ref: Any, *, scan_root: Path, name_index: dict[tuple[str, str], Path]
) -> Path | None:
    """Resolve an MCP server's launch to a local dependency-manifest dir, or None.

    Name-match only (see module docstring): an `npx`/`uvx <pkg>` launch whose
    package matches a local manifest in `name_index` (keyed by `(ecosystem,
    name)` so `npx foo` can't match a PyPI `foo` and vice versa). The matched
    dir must lie within `scan_root` — so an install-root cache entry can't attach
    to a project-scoped MCP in endpoint mode. Remote, external, and every
    local-path / module / exotic launch return `None` (Phase 2 territory).
    """
    install_source = (ref.extra or {}).get("install_source")
    if not isinstance(install_source, str) or not install_source.strip():
        return None
    src = install_source.strip()
    try:
        scan_root = scan_root.resolve()
    except OSError:
        return None

    # Normalize a full-path launcher (`/usr/local/bin/npx`) to its basename so
    # `identity.mcp_package_source` recognizes it (it matches the launcher token
    # exactly, and the basename preserves the `uv tool run` dispatch).
    # Shell-aware tokenization so a quoted launcher path (e.g.
    # `"/Program Files/nodejs/npx" -y @scope/pkg`) yields a clean launcher token;
    # fall back to a naive split if the string isn't valid shell syntax.
    try:
        tokens = shlex.split(src)
    except ValueError:
        tokens = src.split()
    if not tokens:
        return None
    normalized = " ".join([Path(tokens[0]).name, *tokens[1:]])

    # The ONLY resolution path: an npx/uvx package launch. `mcp_package_source`
    # returns None for everything else (remote urls, `node …`, `python -m …`,
    # env-wrapped launchers, …) — all of which Phase 1 declines.
    pkg_source = identity.mcp_package_source(normalized)
    if pkg_source is None:
        return None

    _launcher, ecosystem, package = pkg_source
    name = strip_launch_version(package)
    if ecosystem == "PyPI":
        name = normalize_pypi_name(name)
    matched = name_index.get((ecosystem, name))
    if matched is None:
        return None  # external package — not present in this repo/scan tree
    return matched if _within(matched, scan_root) else None
