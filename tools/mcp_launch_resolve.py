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

import re
import shlex
from pathlib import Path
from typing import Any

from tools import identity

# npx/uvx "runner" packages that execute a local entrypoint passed as a later
# arg (e.g. `npx tsx ./server.ts`). For these, an external (not-locally-present)
# package legitimately delegates to a local path, so Strategy 2 should run. A
# non-runner external package (e.g. `npx @playwright/mcp --config x.json`) IS the
# server itself — its other args are config/flags, not entrypoints — so it must
# resolve to nothing (ADR-0039: external launches resolve to nothing).
_NPX_RUNNERS = frozenset({"tsx", "ts-node"})

# Dep-manifest filenames the nearest-manifest walk recognizes (mirrors
# graph_build._DEP_MANIFEST_PARSERS keys).
_DEP_MANIFEST_FILENAMES = (
    "package.json",
    "package-lock.json",
    "bun.lock",
    "pyproject.toml",
    "uv.lock",
)

# Launcher options that consume the NEXT token as a value (not a positional
# path). Strategy 2's local-path loop skips these values to avoid choosing a
# preload/eval argument as the server entrypoint.
_LAUNCHER_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "node": frozenset({"-r", "--require", "-e", "--eval", "--loader", "--import"}),
    "python": frozenset({"-c"}),
    "python3": frozenset({"-c"}),
}


def normalize_pypi_name(name: str) -> str:
    """Return the canonical comparison form for Python package names."""
    return re.sub(r"[-_.]+", "-", name).lower()


def strip_launch_version(spec: str) -> str:
    """Strip a trailing version pin from an npm or PyPI launch spec.

    npm: `@scope/name@1.0.0` → `@scope/name`, `name@latest` → `name`.
    PyPI: `name==1.2.3` → `name`, `name[extra]==1.2.3` → `name`,
          `name>=1.0` → `name`, `name[extra]>=1,<2` → `name`.
    Unversioned specs are returned unchanged.
    """
    if spec.startswith("@"):
        rest = spec[1:]
        idx = rest.find("@")
        return f"@{rest[:idx]}" if idx != -1 else spec
    idx = spec.find("@")
    if idx != -1:
        return spec[:idx]
    # PyPI requirement spec: strip extras [...] and all PEP 440 version markers
    # (==, >=, <=, !=, ~=, >, <) so `uvx --from my-mcp[server]==1.2.3 my-mcp`
    # resolves to `my-mcp` for the name-index lookup.
    m = re.search(r"[(\[;]|[><=!~]", spec)
    return spec[: m.start()] if m else spec


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
    ref: Any, *, scan_root: Path, name_index: dict[tuple[str, str], Path]
) -> Path | None:
    """Resolve an MCP server ref's launch target to a dependency-manifest dir.

    `name_index` maps `(ecosystem, name)` → directory (see
    `graph_build.build_manifest_name_index`). The ecosystem key (`"npm"` or
    `"PyPI"`) is matched against the launcher so that `npx foo` cannot resolve
    to a `pyproject.toml` named `foo`, and `uvx foo` cannot resolve to a
    `package.json` named `foo`. Returns a directory at/under `scan_root`, or
    `None` when the target is remote, external, or unresolvable.
    """
    install_source = (ref.extra or {}).get("install_source")
    if not isinstance(install_source, str) or not install_source.strip():
        return None
    src = install_source.strip()

    # Resolve scan_root once so the Strategy-1 _within check and _nearest_dep_manifest_dir
    # (Strategy 2) both compare against an absolute path. Without this, a relative
    # scan_root such as Path(".") — produced by `openaca scan repo --target .` — is
    # never found in the parents of a resolved absolute name_index entry, so valid
    # self-launch name matches are silently dropped for the common relative-target case.
    try:
        scan_root = scan_root.resolve()
    except OSError:
        return None

    # Remote: nothing executes locally.
    if src.startswith(("http://", "https://")):
        return None

    try:
        tokens = shlex.split(src)
    except ValueError:
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
        package_name = strip_launch_version(package)
        if _ecosystem == "PyPI":
            package_name = normalize_pypi_name(package_name)
        matched = name_index.get((_ecosystem, package_name))
        if matched is not None:
            # Guard: in endpoint mode the name_index merges install_root and
            # project_root entries. When scan_root=project_root (a project-scoped
            # MCP), a match from install_root must not be returned.
            return matched if _within(matched, scan_root) else None
        # Package not in the local name index — i.e. external. Only a known
        # runner (e.g. `npx tsx ./src/server.ts`) delegates execution to a local
        # entrypoint arg; for those, fall through to Strategy 2. A non-runner
        # external package IS the server itself (`npx @playwright/mcp --config
        # x.json`) — its other args are config/flags, not entrypoints — so it
        # resolves to nothing (ADR-0039), and we must NOT mistake `x.json` for an
        # entrypoint. Gate the fall-through on the runner allowlist.
        if package_name not in _NPX_RUNNERS:
            return None

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
    # Flags that consume the next token as a value (rather than a positional
    # path). Without this, `node -r ./preload.js ./server.js` resolves to the
    # wrong manifest (the preload's dir, not the server's).
    launcher_stem = Path(tokens[0]).stem if tokens else ""
    value_flags = _LAUNCHER_VALUE_FLAGS.get(launcher_stem, frozenset())
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            if tok in value_flags and "=" not in tok:
                skip_next = True
            continue
        # Only treat tokens that look like filesystem paths as candidates; a
        # bare module name (`aiteam.mcp.server`), launcher name (`node`,
        # `python`), or subcommand is not a path. Launcher names have no `/`
        # and don't start with `.`, so this filter naturally skips them while
        # still catching `{"command":"./server.js"}` with no args.
        if "/" not in tok and not tok.startswith("."):
            continue
        candidate = Path(tok) if tok.startswith("/") else (manifest_dir / tok)
        try:
            if candidate.exists():
                result = _nearest_dep_manifest_dir(candidate, scan_root)
                if result is not None:
                    return result
                # candidate exists but is outside scan_root (e.g. /usr/bin/node)
                # or has no dep manifest above it — continue to later tokens.
        except OSError:
            continue
    return None
