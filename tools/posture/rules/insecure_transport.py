"""Posture rule: flag remote MCP endpoints configured over http://.

Stdio MCP servers have no transport-level URL — they're out of scope. Only
remote endpoints (sse, streamableHttp, plain `url` field) are checked.
"""

from __future__ import annotations

from pathlib import Path

from tools.host_paths import resolved_owner
from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-insecure-transport"
TITLE = "Remote MCP endpoint uses insecure transport"
SEVERITY = "medium"
CONFIDENCE = "high"
REMEDIATION = (
    "Configure the MCP endpoint over https://. Plain http:// exposes "
    "prompts, tool calls, and any returned data to network observers and "
    "tampering."
)

_STANDARDS = Standards(
    owasp_app_top_10=["A02:2021"],
    owasp_agentic_top10=["asi04"],
    owasp_mcp_top10=["mcp04:2025"],
)


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


def check_insecure_transport(
    manifests: list[tuple[Path, dict]],
    manifest_hosts: dict[Path, str] | None = None,
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
            if not isinstance(url, str) or not url.startswith("http://"):
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
                        "name": f"{label} @ {url}",
                        "source": {"url": url},
                    },
                    active_in=_infer_hosts(path, manifest, manifest_hosts),
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[{"type": "mcp_server", "name": label}],
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                )
            )
    return findings
