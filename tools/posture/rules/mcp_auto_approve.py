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
        cursor_permissions = manifest.get("cursor_permissions")
        if isinstance(cursor_permissions, dict):
            raw_sources = manifest.get("cursor_permissions_sources")
            sources = raw_sources if isinstance(raw_sources, dict) else {}
            findings.extend(_check_cursor_permissions(path, cursor_permissions, sources))
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
                    active_in=_infer_hosts(manifest),
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[{"type": "mcp_server", "name": label}],
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                )
            )
    return findings


# Cursor expresses this posture in `permissions.json`, not a per-server
# `autoApprove` field on `mcp.json` — `mcpAllowlist` and `autoRun` are the
# same posture the rule exists to report (docs/specs/cursor-agent-kind.md
# "Posture rule applicability"). `manifest` here is already the effective
# (concatenated, both-scopes-merged) view `tools.posture.resolve_cursor_permissions`
# produced, so no precedence logic belongs in this branch.
CURSOR_ALLOW_FIELDS: tuple[str, ...] = ("mcpAllowlist", "autoRun")


def _check_cursor_permissions(
    path: Path,
    permissions: dict,
    sources: dict[str, Path] | None = None,
) -> list[PostureFinding]:
    names: set[str] = set()
    for field in CURSOR_ALLOW_FIELDS:
        value = permissions.get(field)
        if isinstance(value, list):
            names.update(name for name in value if isinstance(name, str))
    name_path = sources or {}
    findings: list[PostureFinding] = []
    for name in sorted(names):
        label = f"mcp-server/{name}"
        declared_path = name_path.get(name, path)
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
                active_in=["cursor"],
                # kind "permissions", not "manifest": `declared_path` is
                # `permissions.json`, a separate policy file that never
                # equals a composed server's `source_manifest` (`mcp.json`).
                # `_attach_bom_ref`'s path-equality gate only applies to
                # `kind == "manifest"` for exactly this reason — this finding
                # must still attach a `bom_ref` via alias matching alone.
                declared_by={"kind": "permissions", "path": str(declared_path)},
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
