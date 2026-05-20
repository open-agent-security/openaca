"""Posture rule: flag MCP server entries that auto-approve tool use."""

from __future__ import annotations

from pathlib import Path

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
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for path, manifest in manifests:
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
            findings.append(
                PostureFinding(
                    rule_id=RULE_ID,
                    title=TITLE,
                    severity=SEVERITY,
                    confidence=CONFIDENCE,
                    component={
                        "type": "mcp_server",
                        "name": f"mcp-server/{name} autoApprove",
                    },
                    active_in=_infer_hosts(manifest),
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[
                        {"type": "mcp_server", "name": f"mcp-server/{name} autoApprove"}
                    ],
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


def _infer_hosts(manifest: dict) -> list[str]:
    if isinstance(manifest.get("mcpServers"), dict):
        return ["claude-code"]
    return []


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
