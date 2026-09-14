"""Posture rule: literal credentials in remote MCP authentication headers."""

from __future__ import annotations

import re
from pathlib import Path

from tools.posture.finding import PostureFinding, Standards
from tools.posture.rules.insecure_transport import _get_server_map

RULE_ID = "openaca-posture-mcp-header-credential"
TITLE = "MCP authentication header contains a literal credential"
SEVERITY = "medium"
CONFIDENCE = "high"
REMEDIATION = (
    "Use the host's environment-backed authentication or OAuth support instead of "
    "storing credentials in MCP configuration. Rotate the credential if the file "
    "was shared or committed. This finding does not establish token validity or exposure."
)
_STANDARDS = Standards(cwe=["CWE-798"])
_AUTH_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "authz", "x-api-key", "api-key", "x-auth-token"}
)
_REFERENCE = re.compile(r"\$\{(?:env:|input:)?[A-Za-z_][A-Za-z0-9_.-]*(?::-)?\}")


def _is_literal(value: object, *, static: bool) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    parts = value.split(maxsplit=1)
    if parts[0].lower() in {"bearer", "basic"}:
        if len(parts) == 1:
            return False
        value = parts[1].strip()
    return static or _REFERENCE.fullmatch(value) is None


def check_mcp_header_credential(manifests: list[tuple[Path, dict]]) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for path, manifest in manifests:
        for name, entry in (_get_server_map(manifest) or {}).items():
            if not isinstance(entry, dict) or entry.get("disabled") is True:
                continue
            if not isinstance(entry.get("url"), str) or not entry["url"]:
                continue
            fields: set[str] = set()
            for key in ("headers", "http_headers"):
                headers = entry.get(key)
                if not isinstance(headers, dict):
                    continue
                for header, value in headers.items():
                    if (
                        isinstance(header, str)
                        and header.lower() in _AUTH_HEADERS
                        and _is_literal(value, static=key == "http_headers")
                    ):
                        fields.add(f"{key}.{header.lower()}")
            if not fields:
                continue
            label = f"mcp-server/{name}"
            findings.append(
                PostureFinding(
                    rule_id=RULE_ID,
                    title=TITLE,
                    severity=SEVERITY,
                    confidence=CONFIDENCE,
                    component={"type": "mcp_server", "name": label},
                    active_in=[],
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[{"type": "mcp_server", "name": label}],
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                    evidence={"fields": sorted(fields)},
                )
            )
    return findings
