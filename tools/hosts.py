"""Host adapter registry (ADR-0044).

A `HostAdapter` records what varies by host for repo-mode discovery:
which manifest patterns belong to it, which posture rules apply to its
manifests, and (for hosts that support it) how to detect the host's
config root on the local machine. `seed_endpoint` is the endpoint-mode
composition entry point: `build_graph`'s endpoint branch calls it once per
selected host with that host's config root (see
docs/specs/multi-host-support.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from tools.component_ref import ComponentRef
from tools.graph import Graph, Node
from tools.parsers import CLAUDE_CODE_MANIFEST_REGISTRY, CURSOR_MANIFEST_REGISTRY, ParserFn

# Local alias, deliberately not imported from `tools.graph_build`: this module
# must stay free of any static dependency on the graph builder.
SourceNormalizer = Callable[[str], str]


class EndpointSeedFn(Protocol):
    """Seed one host's endpoint composition under the target node."""

    def __call__(
        self,
        graph: Graph,
        target: Node,
        config_root: Path,
        project_root: Optional[Path],
        normalize: SourceNormalizer,
        *,
        warnings: Optional[list[str]] = None,
    ) -> None: ...


class EndpointPostureManifestsFn(Protocol):
    """Return the parsed MCP-shaped manifests posture should evaluate for one
    host's endpoint composition, as `(Path, dict)` tuples."""

    def __call__(
        self,
        config_root: Path,
        project_root: Optional[Path],
        refs: list[ComponentRef],
    ) -> list[tuple[Path, dict]]: ...


@dataclass(frozen=True)
class HostAdapter:
    host_id: str
    detect: Callable[[], bool]
    config_root: Callable[[Optional[Path]], Optional[Path]]
    manifest_registry: list[tuple[str, ParserFn]]
    posture_rule_ids: frozenset[str]
    seed_endpoint: Optional[EndpointSeedFn] = None
    collect_endpoint_posture_manifests: Optional[EndpointPostureManifestsFn] = None


def _claude_code_config_root(override: Optional[Path]) -> Optional[Path]:
    if override is not None:
        return override.expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


def _claude_code_detect() -> bool:
    root = _claude_code_config_root(None)
    return root is not None and root.is_dir()


_CLAUDE_CODE_POSTURE_RULE_IDS = frozenset(
    {
        "openaca-posture-insecure-transport",
        "openaca-posture-mcp-auto-approve",
        "openaca-posture-api-endpoint-override",
        "openaca-posture-mutable-install-reference",
        "openaca-posture-skill-executable-tool",
    }
)


def _claude_code_seed_endpoint(
    graph: Graph,
    target: Node,
    config_root: Path,
    project_root: Optional[Path],
    normalize: SourceNormalizer,
    *,
    warnings: Optional[list[str]] = None,
) -> None:
    # Deferred import: a module-level one would cycle
    # (hosts -> endpoint_seeds.claude_code -> graph_build -> hosts).
    from tools.endpoint_seeds.claude_code import seed_endpoint

    seed_endpoint(graph, target, config_root, project_root, normalize, warnings=warnings)


def _claude_code_collect_endpoint_posture_manifests(
    config_root: Path,
    project_root: Optional[Path],
    refs: list[ComponentRef],
) -> list[tuple[Path, dict]]:
    # Deferred import: a module-level one would cycle
    # (hosts -> posture -> ... ); mirrors seed_endpoint's lazy-wrapper pattern.
    from tools.posture import collect_endpoint_mcp_manifests

    return collect_endpoint_mcp_manifests(config_root, project_root, refs)


_CLAUDE_CODE = HostAdapter(
    host_id="claude-code",
    detect=_claude_code_detect,
    config_root=_claude_code_config_root,
    manifest_registry=CLAUDE_CODE_MANIFEST_REGISTRY,
    posture_rule_ids=_CLAUDE_CODE_POSTURE_RULE_IDS,
    seed_endpoint=_claude_code_seed_endpoint,
    collect_endpoint_posture_manifests=_claude_code_collect_endpoint_posture_manifests,
)


def _cursor_config_root(override: Optional[Path]) -> Optional[Path]:
    # Cursor documents no whole-root relocation variable: CURSOR_CONFIG_DIR
    # scopes only the CLI's cli-config.json (cursor.com/docs/cli/reference/
    # configuration), so honoring it here would misread its meaning — only
    # the explicit override param and the default location are supported.
    if override is not None:
        return override.expanduser()
    return Path.home() / ".cursor"


def _cursor_detect() -> bool:
    root = _cursor_config_root(None)
    return root is not None and root.is_dir()


_CURSOR_POSTURE_RULE_IDS = frozenset(
    {
        "openaca-posture-insecure-transport",
        # No "openaca-posture-mcp-auto-approve": verified against Cursor's
        # own MCP docs — approval/auto-run is Run-Modes/UI state there,
        # with no documented per-server manifest field. Asserting an
        # "auto-approval enabled" finding against a Cursor manifest would
        # claim an active posture Cursor's own config surface doesn't
        # support.
        # No "openaca-posture-api-endpoint-override": that rule matches
        # literal Anthropic settings keys (anthropic_base_url,
        # anthropic_auth_token) in Claude's settings.json — a surface
        # Cursor doesn't have in this design (settings collection is
        # Claude-gated) and keys that mean nothing to Cursor.
        "openaca-posture-mutable-install-reference",
        "openaca-posture-skill-executable-tool",
    }
)


def _cursor_seed_endpoint(
    graph: Graph,
    target: Node,
    config_root: Path,
    project_root: Optional[Path],
    normalize: SourceNormalizer,
    *,
    warnings: Optional[list[str]] = None,
) -> None:
    # Deferred import: a module-level one would cycle
    # (hosts -> endpoint_seeds.cursor -> graph_build -> hosts).
    from tools.endpoint_seeds.cursor import seed_endpoint

    seed_endpoint(graph, target, config_root, project_root, normalize, warnings=warnings)


def _cursor_plugin_bundle_roots(refs: list[ComponentRef]) -> list[Path]:
    """Derive each seeded Cursor plugin ref's bundle root directory from its
    self ref's `source_manifest`, mirroring how Claude's collector derives
    plugin install roots from `installPath` — Cursor has no lockfile-backed
    install-state, so the manifest path itself is the only source. Both
    dev-linked and marketplace-cached refs count (ADR-0045 Decision #7 point 5); the
    manifest-less synthesized ref's `source_manifest` (the bundle's
    `.cache-complete` sentinel) resolves to its version-dir bundle root via
    the final `else` branch.
    """
    roots: list[Path] = []
    for ref in refs:
        extra = ref.extra or {}
        if extra.get("component_type") != "plugin" or extra.get("runtime_hosts") != ["cursor"]:
            continue
        if not ref.source_manifest:
            continue
        manifest_path = Path(ref.source_manifest)
        if manifest_path.name == "plugin.json" and manifest_path.parent.name in (
            ".cursor-plugin",
            ".claude-plugin",
        ):
            # Native or Agent Plugins root manifest under a `.cursor-plugin`/
            # `.claude-plugin` dir: the bundle root is two levels up.
            roots.append(manifest_path.parent.parent)
        else:
            # Agent Plugins root manifest directly at the bundle root, or the
            # manifest-less synthesized ref's `.cache-complete` sentinel —
            # either way the bundle root is the manifest's own parent.
            roots.append(manifest_path.parent)
    return roots


def _cursor_collect_endpoint_posture_manifests(
    config_root: Path,
    project_root: Optional[Path],
    refs: list[ComponentRef],
) -> list[tuple[Path, dict]]:
    # Deferred import: same cycle-avoidance rule as the Claude Code binding
    # above — hosts.py must not import tools.posture at module init.
    from tools.posture import collect_mcp_manifests, load_manifest_files

    paths = [config_root / "mcp.json"]
    if project_root is not None:
        paths.append(project_root / ".cursor" / "mcp.json")
    out = load_manifest_files(paths)
    seen = {path.resolve() for path, _ in out}

    for path, data in collect_mcp_manifests(_cursor_plugin_bundle_roots(refs)):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append((path, data))
    return out


_CURSOR = HostAdapter(
    host_id="cursor",
    detect=_cursor_detect,
    config_root=_cursor_config_root,
    manifest_registry=CURSOR_MANIFEST_REGISTRY,
    posture_rule_ids=_CURSOR_POSTURE_RULE_IDS,
    seed_endpoint=_cursor_seed_endpoint,
    collect_endpoint_posture_manifests=_cursor_collect_endpoint_posture_manifests,
)

HOSTS: dict[str, HostAdapter] = {
    "claude-code": _CLAUDE_CODE,
    "cursor": _CURSOR,
}


def all_host_ids() -> list[str]:
    """Every registered host, in registration order."""
    return list(HOSTS.keys())


def detected_hosts() -> list[str]:
    """Registered hosts whose `detect()` is true on this machine."""
    return [host_id for host_id, adapter in HOSTS.items() if adapter.detect()]


# ADR-0045 Decision #7: Cursor plugins are presence-only — enabled/disabled state is never
# observable, so a selection that includes Cursor must never claim "active
# plugin" for its unit count/label. One rule, shared by every endpoint-mode
# surface that reports a plugin unit label (`bom endpoint`, `scan endpoint`),
# so the wording can't drift between them.
PRESENCE_ONLY_PLUGIN_HOSTS = frozenset({"cursor"})


def plugin_unit_label(selected_hosts: list[str]) -> str:
    """ "active plugin" when every selected host can assert active/enabled
    state; "plugin" when any selected host (today: Cursor) is presence-only."""
    return "plugin" if PRESENCE_ONLY_PLUGIN_HOSTS & set(selected_hosts) else "active plugin"
