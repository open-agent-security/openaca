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
            # `_header_owners`/`_component_source` are only trustworthy when
            # `collect_endpoint_settings_manifests` set them itself: it stores
            # `Path` objects, a type raw manifest content parsed via
            # `json.loads` can never produce. `entry` here can otherwise be a
            # raw, unfiltered `mcpServers.<name>` object straight from an
            # untrusted `mcp.json`/`.claude/settings.json` (collected by
            # `collect_mcp_manifests`/`collect_settings_manifests`, which
            # never merge or sanitize keys), so a same-named string value is
            # attacker-controlled and must not redirect `declared_by` or
            # `_attach_bom_ref`'s component match.
            raw_header_owners = entry.get("_header_owners")
            header_owners: dict[str, str] = {}
            if isinstance(raw_header_owners, dict):
                header_owners = {
                    field: str(owner_path)
                    for field, owner_path in raw_header_owners.items()
                    if isinstance(field, str) and isinstance(owner_path, Path)
                }
            raw_component_source = entry.get("_component_source")
            component_source = (
                str(raw_component_source) if isinstance(raw_component_source, Path) else None
            )
            # `collect_endpoint_settings_manifests` deep-merges a server entry
            # from every scope that touches it, so `headers`/`http_headers`
            # can combine keys owned by different scope files. Grouping by
            # `header_owners` (falling back to this manifest's own path when
            # absent, e.g. a plain repo `mcp.json`) keeps a credential
            # attributed to the scope that actually declared it rather than
            # collapsing every flagged header into one finding on whichever
            # scope the manifest tuple happens to be keyed under.
            fields_by_owner: dict[str, set[str]] = {}
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
                        # `header_owners` is keyed by the header's exact
                        # spelling (see `_mcp_server_header_field_owners`) so
                        # that `Authorization` and `authorization` set by
                        # different scopes on the same server don't collide
                        # into one owner. The evidence field name is still
                        # lowercased for a stable, human-readable report.
                        evidence_field = f"{key}.{header.lower()}"
                        owner = header_owners.get(f"{key}.{header}", str(path))
                        fields_by_owner.setdefault(owner, set()).add(evidence_field)
            if not fields_by_owner:
                continue
            label = f"mcp-server/{name}"
            for owner_path, fields in fields_by_owner.items():
                findings.append(
                    PostureFinding(
                        rule_id=RULE_ID,
                        title=TITLE,
                        severity=SEVERITY,
                        confidence=CONFIDENCE,
                        component={"type": "mcp_server", "name": label},
                        active_in=[],
                        declared_by={"kind": "manifest", "path": owner_path},
                        component_path=[{"type": "mcp_server", "name": label}],
                        standards=_STANDARDS,
                        remediation=REMEDIATION,
                        evidence={"fields": sorted(fields)},
                        component_source=component_source,
                    )
                )
    return findings
