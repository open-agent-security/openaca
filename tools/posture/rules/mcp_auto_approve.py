"""Posture rule: flag MCP server entries that auto-approve tool use."""

from __future__ import annotations

from pathlib import Path

from tools.host_paths import resolved_owner
from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-mcp-auto-approve"
TITLE = "MCP server has auto-approval enabled"
SEVERITY = "medium"
CONFIDENCE = "medium"
REMEDIATION = (
    "Remove MCP auto-approval or restrict it to the smallest explicit tool set. "
    "Auto-approval lets an MCP server execute approved actions without normal "
    "per-use confirmation."
)

_STANDARDS = Standards(
    owasp_agentic_top10=["asi03"],
    owasp_mcp_top10=["mcp07:2025"],
)


def check_mcp_auto_approve(
    manifests: list[tuple[Path, dict]],
    manifest_hosts: dict[Path, str] | None = None,
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for path, manifest in manifests:
        if resolved_owner(path, manifest_hosts) != "claude-code":
            # autoApprove is a Claude Code mcp.json convention with no
            # documented Cursor equivalent (verified against Cursor's
            # own MCP docs — approval there is Run-Modes/UI state).
            # A manifest belonging to another host carrying this key
            # isn't evidence of an active posture on that host.
            continue
        servers = _get_server_map(manifest)
        if servers is None:
            continue
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            if entry.get("disabled") is True:
                continue
            auto_approve = entry.get("autoApprove")
            if not _is_enabled(auto_approve):
                continue
            label = f"mcp-server/{name}"
            findings.append(
                PostureFinding(
                    rule_id=RULE_ID,
                    title=TITLE,
                    severity=SEVERITY,
                    confidence=CONFIDENCE,
                    component={
                        "type": "mcp_server",
                        "name": f"{label} autoApprove",
                    },
                    active_in=_infer_hosts(path, manifest, manifest_hosts),
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[{"type": "mcp_server", "name": label}],
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                )
            )
    return findings


def _is_enabled(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, list) and value:
        return True
    return False


def _infer_hosts(
    path: Path, manifest: dict, manifest_hosts: dict[Path, str] | None = None
) -> list[str]:
    """`mcpServers` is the shape both Claude Code and Cursor use — content
    alone can't tell them apart, but `resolved_owner` (collection
    provenance, falling back to path shape) always can. Other shapes
    (`servers` for VS Code, flat-root) carry no host signal at all; leave
    active_in empty rather than guess."""
    if not isinstance(manifest.get("mcpServers"), dict):
        return []
    return [resolved_owner(path, manifest_hosts)]


def _get_server_map(manifest: dict) -> dict | None:
    for key in ("mcpServers", "servers"):
        val = manifest.get(key)
        if isinstance(val, dict):
            return val
    if manifest and all(
        isinstance(v, dict) and ("command" in v or "url" in v) for v in manifest.values()
    ):
        return manifest
    return None
