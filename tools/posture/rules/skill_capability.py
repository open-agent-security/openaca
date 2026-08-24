"""Posture rule: flag skills that declare executable tool access."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tools.active_in import active_in
from tools.capability_extract import _allowed_tools, _executable_tool_base
from tools.component_ref import ComponentRef, canonical_component_identity
from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-skill-executable-tool"
TITLE = "Skill declares executable tool access"
SEVERITY = "low"
CONFIDENCE = "high"
REMEDIATION = (
    "Review whether this skill needs executable tool access and keep it under "
    "normal code-review/change-control."
)

EXECUTABLE_TOOLS = {"bash", "shell"}

_STANDARDS = Standards(owasp_agentic_top10=["asi03"])


def check_skill_executable_tools(
    refs: list[ComponentRef], *, agent_kind: str | None = None
) -> list[PostureFinding]:
    findings: list[PostureFinding] = []
    for ref in refs:
        if (ref.extra or {}).get("component_type") != "skill":
            continue
        finding = _allowed_executable_tool_finding(ref, agent_kind=agent_kind)
        if finding is not None:
            findings.append(finding)
    return findings


def _allowed_executable_tool_finding(
    ref: ComponentRef, *, agent_kind: str | None = None
) -> PostureFinding | None:
    frontmatter = _read_frontmatter(Path(ref.source_manifest))
    allowed_tools = _allowed_tools(frontmatter)
    executable = sorted(
        tool for tool in allowed_tools if _executable_tool_base(tool).lower() in EXECUTABLE_TOOLS
    )
    if not executable:
        return None
    identity = canonical_component_identity(ref)
    name = ref.name or identity or "skill"
    component: dict[str, Any] = {
        "name": name,
        "type": "skill",
    }
    if identity is not None:
        component["identity"] = identity
    return PostureFinding(
        rule_id=RULE_ID,
        title=TITLE,
        severity=SEVERITY,
        confidence=CONFIDENCE,
        component=component,
        active_in=active_in(ref, agent_kind=agent_kind),
        declared_by=(ref.extra or {}).get("declared_by")
        if isinstance((ref.extra or {}).get("declared_by"), dict)
        else {"kind": "manifest", "path": ref.source_manifest},
        component_path=_component_path(ref),
        standards=_STANDARDS,
        remediation=REMEDIATION,
        evidence={"allowed_tools": executable},
        bom_ref=_bom_ref_for(ref),
    )


def _bom_ref_for(ref: ComponentRef) -> str | None:
    value = (ref.extra or {}).get("bom_ref")
    return value if isinstance(value, str) and value else None


def _read_frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        loaded = yaml.safe_load(text[3:end].strip())
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _component_path(ref: ComponentRef) -> list[dict[str, str]]:
    raw = (ref.extra or {}).get("component_path")
    if isinstance(raw, list):
        return [
            {"type": str(item.get("type")), "name": str(item.get("name"))}
            for item in raw
            if isinstance(item, dict)
            and item.get("type") is not None
            and item.get("name") is not None
        ]
    name = ref.name or canonical_component_identity(ref)
    return [{"type": "skill", "name": name}] if name else []
