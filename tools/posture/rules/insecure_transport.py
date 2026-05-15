"""Posture rule: flag remote MCP endpoints configured over http://.

Stdio MCP servers have no transport-level URL — they're out of scope. Only
remote endpoints (sse, streamableHttp, plain `url` field) are checked.
"""

from __future__ import annotations

from pathlib import Path

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


def check_insecure_transport(
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for path, manifest in manifests:
        for envelope_key in ("mcpServers", "servers"):
            envelope = manifest.get(envelope_key)
            if not isinstance(envelope, dict):
                continue
            for name, entry in envelope.items():
                if not isinstance(entry, dict):
                    continue
                url = entry.get("url")
                if not isinstance(url, str) or not url.startswith("http://"):
                    continue
                findings.append(
                    PostureFinding(
                        rule_id=RULE_ID,
                        title=TITLE,
                        severity=SEVERITY,
                        confidence=CONFIDENCE,
                        component=f"mcp-server/{name} @ {url}",
                        location=str(path),
                        standards=_STANDARDS,
                        remediation=REMEDIATION,
                    )
                )
    return findings
