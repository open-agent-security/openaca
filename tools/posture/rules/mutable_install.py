"""Posture rule: flag agent components installed from a mutable source ref.

Applies to MCP servers, plugins, and skills equally — anything that can be
installed from a remote source by reference. Local checked-in paths are
exempt (the immutability helper returns False for them).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

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
        install_source = _mutable_install_source_for(ref)
        if install_source is None:
            continue
        findings.append(
            PostureFinding(
                rule_id=RULE_ID,
                title=TITLE,
                severity=SEVERITY,
                confidence=CONFIDENCE,
                component=_component_for(ref, install_source),
                active_in=_active_in_for(ref),
                declared_by=_declared_by_for(ref),
                component_path=_component_path_for(ref, install_source),
                standards=_standards_for(ref),
                remediation=REMEDIATION,
            )
        )
    return findings


def _mutable_install_source_for(ref: ComponentRef) -> str | None:
    install_source = (ref.extra or {}).get("install_source")
    if isinstance(install_source, str) and install_source and is_mutable_reference(install_source):
        return install_source

    if ref.ecosystem != "claude-plugin":
        return None
    if ref.version and ref.version != "unknown":
        return None
    git_sha = (ref.extra or {}).get("gitCommitSha")
    if isinstance(git_sha, str) and git_sha.strip():
        return None
    if ref.component_identity:
        if ref.component_identity.endswith("@unknown"):
            return ref.component_identity
        return f"{ref.component_identity}@unknown"
    if ref.name:
        return f"claude-plugin/{ref.name}@unknown"
    return None


def _component_for(ref: ComponentRef, install_source: str) -> dict[str, Any]:
    component: dict[str, Any] = {
        "type": _component_type_for(ref),
        "name": _format_component(ref, install_source),
    }
    if ref.purl:
        component["source"] = {"purl": ref.purl}
    return component


def _active_in_for(ref: ComponentRef) -> list[str]:
    runtime_hosts = (ref.extra or {}).get("runtime_hosts")
    if isinstance(runtime_hosts, list):
        return [h for h in runtime_hosts if isinstance(h, str)]
    return []


def _declared_by_for(ref: ComponentRef) -> dict | None:
    declared_by = (ref.extra or {}).get("declared_by")
    if isinstance(declared_by, dict):
        return dict(declared_by)
    if ref.source_manifest:
        return {"kind": "manifest", "path": ref.source_manifest}
    return None


def _component_path_for(ref: ComponentRef, install_source: str) -> list[dict[str, str]]:
    component_path = (ref.extra or {}).get("component_path")
    if isinstance(component_path, list):
        out = []
        for item in component_path:
            if isinstance(item, dict):
                typ = item.get("type")
                name = item.get("name")
                if isinstance(typ, str) and isinstance(name, str):
                    out.append({"type": typ, "name": name})
        if out:
            return out
    return [{"type": _component_type_for(ref), "name": _format_component(ref, install_source)}]


def _component_type_for(ref: ComponentRef) -> str:
    extra_type = (ref.extra or {}).get("component_type")
    if isinstance(extra_type, str) and extra_type:
        return extra_type
    return "component"


def _format_component(ref: ComponentRef, install_source: str) -> str:
    if ref.ecosystem == "claude-plugin" and install_source.startswith("claude-plugin/"):
        return install_source
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
