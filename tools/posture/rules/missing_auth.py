"""Posture rule: flag remote MCP endpoints with no visible auth material.

Confidence is medium, not high — auth may be configured out-of-band (system
keyring, ambient credentials, proxy auth). The rule surfaces "I cannot see
any auth here" as a prompt to verify, not as an assertion of misconfiguration.
"""

from __future__ import annotations

from pathlib import Path

from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-missing-remote-auth"
TITLE = "Remote MCP endpoint has no visible auth material"
SEVERITY = "low"
CONFIDENCE = "medium"
REMEDIATION = (
    "If this endpoint requires auth, declare it in the manifest "
    "(headers, env, or token fields). If auth is provided out-of-band "
    "(keyring, ambient credentials), this finding can be suppressed for "
    "this entry."
)

_STANDARDS = Standards(
    owasp_app_top_10=["A01:2021", "A07:2021"],
    owasp_agentic_top10=["asi03"],
    owasp_mcp_top10=["mcp07:2025"],
)


def _get_server_map(manifest: dict) -> dict | None:
    """Return the server dict from mcpServers, servers, or flat root."""
    for key in ("mcpServers", "servers"):
        val = manifest.get(key)
        if isinstance(val, dict):
            return val
    # Flat root: no envelope wrapper; all values are server-shaped dicts.
    if manifest and all(
        isinstance(v, dict) and ("command" in v or "url" in v) for v in manifest.values()
    ):
        return manifest
    return None


def check_missing_auth(
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
            url = entry.get("url")
            if not isinstance(url, str) or not url:
                continue
            if _has_auth_material(entry):
                continue
            findings.append(
                PostureFinding(
                    rule_id=RULE_ID,
                    title=TITLE,
                    severity=SEVERITY,
                    confidence=CONFIDENCE,
                    component={
                        "type": "mcp_server",
                        "name": f"mcp-server/{name} @ {url}",
                        "source": {"url": url},
                    },
                    active_in=["claude-code"],
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[{"type": "mcp_server", "name": f"mcp-server/{name} @ {url}"}],
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                )
            )
    return findings


def _has_auth_material(entry: dict) -> bool:
    headers = entry.get("headers")
    if isinstance(headers, dict) and any(
        isinstance(k, str) and k.lower() == "authorization" for k in headers
    ):
        return True
    if entry.get("env") or entry.get("token") or entry.get("apiKey"):
        return True
    return False
