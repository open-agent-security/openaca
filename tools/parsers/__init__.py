"""Manifest parser registry."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from tools.component_ref import ComponentRef
from tools.parsers import (
    bun_lock,
    claude_command_agent,
    claude_plugin,
    claude_settings,
    claude_skill,
    mcp_json,
    package_json,
    package_lock_json,
    pyproject_toml,
    uv_lock,
)
from tools.parsers.gitignore import is_ignored, iter_unignored_files, load_gitignore_spec

ParserFn = Callable[[Path], list[ComponentRef]]


def _parse_repo_command(path: Path) -> list[ComponentRef]:
    return claude_command_agent.parse_file(path, kind="command")


def _parse_repo_agent(path: Path) -> list[ComponentRef]:
    return claude_command_agent.parse_file(path, kind="agent")


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
    # Plan 008: agent-component inventory in repo mode. These
    # surfaces emit the same ecosystems as endpoint mode; parentage is set by
    # the graph edge, not stored on the refs.
    ("**/.claude/skills/*/SKILL.md", claude_skill.parse),
    ("**/.claude/commands/**/*.md", _parse_repo_command),
    ("**/.claude/agents/**/*.md", _parse_repo_agent),
    # Plan 009: lockfile parsers for repo-mode transitive coverage.
    # extra["transitive"]=True so SARIF surfaces properties.coverage=transitive.
    ("package-lock.json", package_lock_json.parse),
    ("uv.lock", uv_lock.parse),
    ("bun.lock", bun_lock.parse),
]


def _registry_pattern_matches(path: Path, root: Path, pattern: str) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path

    if "/" not in pattern and "*" not in pattern:
        return rel.name == pattern

    rel_parts = rel.parts
    rel_posix = rel.as_posix()
    if pattern in {".claude-plugin/plugin.json", ".claude/settings.json"}:
        return rel_posix == pattern or rel_posix.endswith(f"/{pattern}")

    if len(rel_parts) < 4 or rel_parts[-1] != "SKILL.md":
        skill_match = False
    else:
        skill_match = any(
            rel_parts[i] == ".claude"
            and i + 3 < len(rel_parts)
            and rel_parts[i + 1] == "skills"
            and i + 3 == len(rel_parts) - 1
            for i in range(len(rel_parts) - 3)
        )
    if pattern == "**/.claude/skills/*/SKILL.md":
        return skill_match

    if pattern in {"**/.claude/commands/**/*.md", "**/.claude/agents/**/*.md"}:
        kind = "commands" if "commands" in pattern else "agents"
        return rel.suffix == ".md" and any(
            rel_parts[i] == ".claude" and i + 2 < len(rel_parts) and rel_parts[i + 1] == kind
            for i in range(len(rel_parts) - 2)
        )

    return rel.match(pattern)


def _filter_secondary_refs(
    refs: list[ComponentRef],
    primary: Path,
    root: Path,
    spec,
) -> list[ComponentRef]:
    """Drop refs whose source_manifest is a secondary gitignored file.

    Some parsers (e.g. claude_plugin when mcpServers is a string path)
    follow references to other files on disk. Those secondary files bypass
    the rglob filter applied in parse_repo_grouped, so we re-apply the
    same spec check here. Refs from the primary file are always kept.

    When spec=None (include_gitignored=True), is_ignored only blocks .git/
    paths — consistent with the rglob-hit filtering logic above.
    """
    primary_resolved = primary.resolve()
    root_resolved = root.resolve()
    out: list[ComponentRef] = []
    for r in refs:
        if not r.source_manifest:
            out.append(r)
            continue
        src = Path(r.source_manifest).resolve()
        if src == primary_resolved:
            out.append(r)
            continue
        try:
            rel = src.relative_to(root_resolved)
        except ValueError:
            out.append(r)  # outside root; path safety enforced by the parser
            continue
        if not is_ignored(rel, spec):
            out.append(r)
    return out


def parse_repo_grouped(
    root: Path,
    include_gitignored: bool = False,
) -> tuple[list[tuple[Path, list[ComponentRef]]], int]:
    """Walk `root` and return (per-manifest results, total paths matched).

    The second element counts every path that matched a registry pattern AND
    survived `.gitignore` filtering. Callers use this to distinguish "target
    had no manifests at all" (n_found == 0) from "target had manifests that
    all failed to parse" (n_found > 0 but grouped is empty).

    By default, paths matching entries in `<root>/.gitignore` are excluded —
    typical repos pull `node_modules/`, `.venv/`, `dist/`, etc. into rglob
    hits and emit noisy/wrong findings (a vendored `package.json` deep inside
    `node_modules/` shouldn't be attributed to the host repo). Set
    `include_gitignored=True` to walk those anyway (e.g., to audit a vendored
    dependency tree). `.git/` is always skipped.

    Per-path parse failures are silently dropped — these parsers run against
    arbitrary user repos and one malformed file should not abort the rest of
    the scan. Manifests with zero components are still included so consumers
    can see the file was visited.

    Per-manifest groups preserve duplicates intentionally — verbose output
    should show what each manifest declared, even if another manifest's parse
    path discovered the same component. Use `flatten_grouped` (or `parse_repo`)
    when a deduplicated cross-manifest ref list is needed for matching/SARIF;
    those callers want one finding per logical component, not per discovery
    path.
    """
    spec = None if include_gitignored else load_gitignore_spec(root)
    grouped: list[tuple[Path, list[ComponentRef]]] = []
    n_found = 0
    for path in iter_unignored_files(root, spec):
        for pattern, parser in REGISTRY:
            if not _registry_pattern_matches(path, root, pattern):
                continue
            n_found += 1
            try:
                refs = parser(path)
                refs = _filter_secondary_refs(refs, path, root, spec)
                grouped.append((path, refs))
            except Exception:
                continue
    return grouped, n_found


def flatten_grouped(
    grouped: list[tuple[Path, list[ComponentRef]]],
) -> list[ComponentRef]:
    """Flatten per-manifest groups into a deduplicated ref list.

    The same logical component can be discovered via multiple registry paths
    — e.g., a `.mcp.json` walked directly AND followed indirectly through a
    `.claude-plugin/plugin.json` whose `mcpServers` is the string path
    `"./.mcp.json"`. Both routes emit identical refs (same source_manifest +
    source_locator + identity). Without dedup, matching produces duplicate
    findings and SARIF emits duplicate results.

    Dedup key intentionally excludes `extra` (a dict, so unhashable; also
    discovery-path-dependent in some cases). What identifies a logical component
    for matching is the (where, what) tuple:
    (source_manifest, source_locator, ecosystem, name, version, component_identity).
    The first route to discover a key wins; attribution is no longer a ref field
    (it is derived from the graph), so there is no attributed-vs-unattributed
    preference to apply.
    """
    refs: list[ComponentRef] = []
    seen: set[tuple] = set()
    for _, group in grouped:
        for r in group:
            # Resolve source_manifest to an absolute path so that relative
            # and absolute references to the same file collapse to the same
            # key. Without this, `--target .` produces a relative path from
            # the direct rglob hit while _parse_mcp_servers_from_plugin_json
            # calls Path.resolve() internally, yielding different strings for
            # the same file and breaking dedup.
            manifest_key = str(Path(r.source_manifest).resolve()) if r.source_manifest else ""
            key = (
                manifest_key,
                r.source_locator,
                r.ecosystem,
                r.name,
                r.version,
                r.component_identity,
            )
            if key in seen:
                continue
            seen.add(key)
            refs.append(r)
    return refs


def parse_repo(root: Path, include_gitignored: bool = False) -> list[ComponentRef]:
    """Walk `root` and return deduplicated ComponentRefs from all known manifests."""
    grouped, _ = parse_repo_grouped(root, include_gitignored=include_gitignored)
    return flatten_grouped(grouped)
