"""Scanner-side posture-finding rules (plan 014, ADR-0009).

Posture findings are emitted by the scanner only — they never become overlay
records, never mint OpenACA IDs, and never change the corpus schema. They
carry a `standards{}` block (CWE / OpenSSF Scorecard / SLSA / OWASP) in
scanner output. Gated behind `--include-posture` to keep the default scan
output strictly vulnerability findings.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers.gitignore import is_ignored, load_gitignore_spec
from tools.posture.finding import PostureFinding, Standards
from tools.posture.rules import (
    api_endpoint_override,
    insecure_transport,
    mcp_auto_approve,
    mutable_install,
)

__all__ = [
    "PostureFinding",
    "Standards",
    "collect_endpoint_mcp_manifests",
    "collect_endpoint_settings_manifests",
    "collect_mcp_manifests",
    "collect_settings_manifests",
    "run_posture_rules",
]


_MCP_MANIFEST_NAMES: frozenset[str] = frozenset(
    {"mcp.json", ".mcp.json", "claude_desktop_config.json"}
)
_PLUGIN_MANIFEST_NAME = "plugin.json"
_PLUGIN_MANIFEST_PARENT_DIR = ".claude-plugin"


def run_posture_rules(
    refs: list[ComponentRef],
    manifests: list[tuple[Path, dict]],
    settings_manifests: list[tuple[Path, dict]] | None = None,
) -> list[PostureFinding]:
    """Run all V0 posture rules and concatenate their findings."""
    settings_manifests = settings_manifests or []
    findings: list[PostureFinding] = []
    findings.extend(mutable_install.check_mutable_install(refs))
    findings.extend(insecure_transport.check_insecure_transport(manifests))
    findings.extend(mcp_auto_approve.check_mcp_auto_approve(manifests + settings_manifests))
    findings.extend(api_endpoint_override.check_api_endpoint_override(settings_manifests))
    return findings


def collect_mcp_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
) -> list[tuple[Path, dict]]:
    """Walk one or more roots for MCP-shaped manifests and return parsed dicts.

    Used by URL-shape rules that need the raw manifest to inspect
    `mcpServers[*].url` and adjacent fields. Parse failures are silently
    dropped — these rules are best-effort and should never abort a scan.

    `.git/` is always skipped regardless of `include_gitignored`, consistent
    with the main repo scanner (`parse_repo_grouped`). When
    `include_gitignored=False`, paths matched by `<root>/.gitignore` are also
    skipped, keeping posture scope consistent with the main repo scan.
    """
    out: list[tuple[Path, dict]] = []
    seen: set[Path] = set()
    for root in roots:
        if root is None or not root.exists():
            continue
        spec = None if include_gitignored else load_gitignore_spec(root)
        for name in _MCP_MANIFEST_NAMES:
            for path in root.rglob(name):
                if is_ignored(path.relative_to(root), spec):
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(data, dict):
                    out.append((path, data))
        for path in root.rglob(_PLUGIN_MANIFEST_NAME):
            if path.parent.name != _PLUGIN_MANIFEST_PARENT_DIR:
                continue
            if is_ignored(path.relative_to(root), spec):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append((path, data))
    return out


def collect_settings_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    seen: set[Path] = set()
    for root in roots:
        if root is None or not root.exists():
            continue
        spec = None if include_gitignored else load_gitignore_spec(root)
        for path in root.rglob("settings.json"):
            if path.parent.name != ".claude":
                continue
            if is_ignored(path.relative_to(root), spec):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append((path, data))
    return out


def collect_endpoint_mcp_manifests(
    config_dir: Path,
    project_root: Path | None,
    refs: list[ComponentRef],
) -> list[tuple[Path, dict]]:
    """Collect MCP manifests that belong to the resolved endpoint inventory.

    Endpoint mode is install-state-aware. The Claude config directory also
    contains marketplace catalogs and stale cache versions, so recursively
    walking the whole directory would report posture findings for components
    that are not active on the endpoint.
    """
    roots: list[Path] = []
    for ref in refs:
        if (ref.extra or {}).get("component_type") != "plugin":
            continue
        install_path = ref.extra.get("installPath")
        if isinstance(install_path, str) and install_path:
            roots.append(Path(install_path))

    out = collect_mcp_manifests(roots)
    seen = {path.resolve() for path, _ in out}

    direct_paths = [
        config_dir / ".mcp.json",
        config_dir / "mcp.json",
        config_dir / "claude_desktop_config.json",
    ]
    if project_root is not None:
        direct_paths.append(project_root / ".mcp.json")

    for path in direct_paths:
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append((path, data))

    return out


def collect_endpoint_settings_manifests(
    config_dir: Path,
    project_root: Path | None,
) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    paths = [config_dir / "settings.json"]
    if project_root is not None:
        paths.extend(
            [
                project_root / ".claude" / "settings.json",
                project_root / ".claude" / "settings.local.json",
            ]
        )
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append((path, data))
    return out
