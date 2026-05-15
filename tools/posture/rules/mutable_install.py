"""Posture rule: flag agent components installed from a mutable source ref.

Applies to MCP servers, plugins, and skills equally — anything that can be
installed from a remote source by reference. Local checked-in paths are
exempt (the immutability helper returns False for them).
"""

from __future__ import annotations

from dataclasses import replace

from tools.component_ref import ComponentRef
from tools.posture.finding import PostureFinding, Standards
from tools.posture.immutability import is_mutable_reference

RULE_ID = "openaca-posture-mutable-install-reference"
TITLE = "Component installed from a mutable source reference"
SEVERITY = "low"
CONFIDENCE = "high"
REMEDIATION = (
    "Pin the install reference to an exact version, commit SHA, or Docker "
    "digest. Mutable refs (no version, @latest, branch refs, missing digest) "
    "can roll forward to unexpected code at any time."
)

_BASE_STANDARDS = Standards(
    cwe=["CWE-1357"],
    openssf_scorecard=["Pinned-Dependencies"],
    slsa=["immutable-references"],
    owasp_agentic_top10=["asi04"],
)


def check_mutable_install(refs: list[ComponentRef]) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for ref in refs:
        install_source = (ref.extra or {}).get("install_source")
        if not isinstance(install_source, str) or not install_source:
            continue
        if not is_mutable_reference(install_source):
            continue
        findings.append(
            PostureFinding(
                rule_id=RULE_ID,
                title=TITLE,
                severity=SEVERITY,
                confidence=CONFIDENCE,
                component=_format_component(ref, install_source),
                location=ref.source_manifest or "",
                standards=_standards_for(ref),
                remediation=REMEDIATION,
            )
        )
    return findings


def _format_component(ref: ComponentRef, install_source: str) -> str:
    if ref.ecosystem and ref.name:
        return f"{ref.ecosystem}/{ref.name} ({install_source})"
    if ref.component_identity:
        return f"{ref.component_identity} ({install_source})"
    return install_source


def _standards_for(ref: ComponentRef) -> Standards:
    """Add the MCP-specific taxonomy code when the component is MCP-shaped.

    Identified by either an `mcp-stdio/` component_identity or a name that
    starts with `mcp-` / contains `mcp-server` — the existing parser
    conventions. Plugins and skills (non-MCP) get only the base standards.
    """
    is_mcp = _looks_like_mcp(ref)
    if not is_mcp:
        return _BASE_STANDARDS
    return replace(_BASE_STANDARDS, owasp_mcp_top10=["mcp04:2025"])


def _looks_like_mcp(ref: ComponentRef) -> bool:
    if ref.component_identity and ref.component_identity.startswith("mcp-stdio/"):
        return True
    name = (ref.name or "").lower()
    if "mcp" in name:
        return True
    return False
