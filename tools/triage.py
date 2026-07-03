"""Component-centric triage over structured OpenACA scan JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Priority = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]
TriageAction = Literal["remove", "pin", "upgrade", "approve", "replace", "accept", "review"]

_AGENT_COMPONENT_TYPES = {"plugin", "mcp_server", "skill", "hook", "command", "agent"}
_SEVERITY_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1, "none": 0}
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class TriageEvidence:
    finding_type: str
    id: str
    title: str
    severity: Priority
    confidence: Confidence
    provenance: str
    source: str | None = None
    fixed_in: str | None = None
    remediation: str | None = None
    component: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "finding_type": self.finding_type,
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "confidence": self.confidence,
            "provenance": self.provenance,
            "component": self.component,
        }
        if self.source:
            out["source"] = self.source
        if self.fixed_in:
            out["fixed_in"] = self.fixed_in
        if self.remediation:
            out["remediation"] = self.remediation
        return out


@dataclass(frozen=True)
class TriageCard:
    component_id: str
    component_label: str
    component_type: str
    rank: int
    priority: Priority
    confidence: Confidence
    action: TriageAction
    composition_path: list[dict[str, str]]
    evidence: list[TriageEvidence]
    why_it_matters: str
    scope_limits: list[str]
    active_in: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "component_label": self.component_label,
            "component_type": self.component_type,
            "rank": self.rank,
            "priority": self.priority,
            "confidence": self.confidence,
            "action": self.action,
            "composition_path": self.composition_path,
            "evidence": [item.to_dict() for item in self.evidence],
            "why_it_matters": self.why_it_matters,
            "scope_limits": self.scope_limits,
            "active_in": self.active_in,
        }


def build_triage_cards(scan_doc: dict[str, Any]) -> list[TriageCard]:
    """Return deterministic component-centric exposure cards.

    The input is the structured JSON emitted by `openaca scan --format json`.
    Triage does not re-read targets or query advisory sources.
    """
    findings = scan_doc.get("findings")
    if not isinstance(findings, list):
        raise ValueError("scan JSON must contain findings[]")

    grouped: dict[str, _CardDraft] = {}
    for raw in findings:
        if not isinstance(raw, dict):
            continue
        evidence = _evidence_from_finding(raw)
        if evidence is None:
            continue
        selected = _select_component(raw)
        draft = grouped.setdefault(
            selected.component_id,
            _CardDraft(
                component_id=selected.component_id,
                component_label=selected.component_label,
                component_type=selected.component_type,
                composition_path=selected.composition_path,
                active_in=_active_in(raw),
            ),
        )
        draft.evidence.append(evidence)
        draft.active_in = sorted(set(draft.active_in).union(_active_in(raw)))

    ranked = sorted(grouped.values(), key=_draft_sort_key)
    cards: list[TriageCard] = []
    for index, draft in enumerate(ranked, start=1):
        priority = _draft_priority(draft)
        confidence = _draft_confidence(draft)
        action = _draft_action(draft)
        cards.append(
            TriageCard(
                component_id=draft.component_id,
                component_label=draft.component_label,
                component_type=draft.component_type,
                rank=index,
                priority=priority,
                confidence=confidence,
                action=action,
                composition_path=draft.composition_path,
                evidence=sorted(draft.evidence, key=_evidence_sort_key),
                why_it_matters=_why_it_matters(draft, action),
                scope_limits=_scope_limits(draft),
                active_in=draft.active_in,
            )
        )
    return cards


@dataclass
class _SelectedComponent:
    component_id: str
    component_label: str
    component_type: str
    composition_path: list[dict[str, str]]


@dataclass
class _CardDraft:
    component_id: str
    component_label: str
    component_type: str
    composition_path: list[dict[str, str]]
    active_in: list[str] = field(default_factory=list)
    evidence: list[TriageEvidence] = field(default_factory=list)


def _evidence_from_finding(raw: dict[str, Any]) -> TriageEvidence | None:
    finding_type = _as_str(raw.get("finding_type")) or "finding"
    if finding_type == "vulnerability":
        evidence_id = _as_str(raw.get("id")) or _advisory_id(raw) or "unknown-advisory"
        return TriageEvidence(
            finding_type=finding_type,
            id=evidence_id,
            title=_as_str(raw.get("summary")) or _as_str(raw.get("title")) or evidence_id,
            severity=_priority(raw.get("severity")),
            confidence=_confidence(raw.get("confidence")),
            provenance="advisory-derived",
            source=_as_str(raw.get("source")) or _matched_source(raw),
            fixed_in=_as_str(raw.get("fixed_in")),
            component=_component(raw),
        )
    if finding_type == "posture":
        evidence_id = _as_str(raw.get("rule_id")) or "unknown-posture"
        source = _as_str(raw.get("source")) or "openaca"
        provenance = "scanner-derived" if source == "openaca" else "external-scanner-derived"
        return TriageEvidence(
            finding_type=finding_type,
            id=evidence_id,
            title=_as_str(raw.get("title")) or evidence_id,
            severity=_priority(raw.get("severity")),
            confidence=_confidence(raw.get("confidence")),
            provenance=provenance,
            source=source,
            remediation=_as_str(raw.get("remediation")),
            component=_component(raw),
        )
    if finding_type == "observation":
        evidence_id = _as_str(raw.get("observation_id")) or "unknown-observation"
        source = _as_str(raw.get("source")) or "unknown"
        provenance = "scanner-derived" if source == "openaca" else "external-scanner-derived"
        return TriageEvidence(
            finding_type=finding_type,
            id=evidence_id,
            title=_as_str(raw.get("title")) or evidence_id,
            severity=_priority(raw.get("severity")),
            confidence=_confidence(raw.get("confidence")),
            provenance=provenance,
            source=source,
            remediation=_as_str(raw.get("remediation")),
            component=_component(raw),
        )
    return None


def _select_component(raw: dict[str, Any]) -> _SelectedComponent:
    path = _component_path(raw)
    for item in path:
        if item.get("type") in _AGENT_COMPONENT_TYPES:
            component_type = item["type"]
            label = item["name"]
            component_id = _component_id(raw, component_type, label)
            return _SelectedComponent(component_id, label, component_type, path)
    component = _component(raw)
    component_type = _as_str(component.get("type")) or "component"
    label = _as_str(component.get("name")) or "<unidentified>"
    return _SelectedComponent(
        _component_id(raw, component_type, label), label, component_type, path
    )


def _component_id(raw: dict[str, Any], component_type: str, label: str) -> str:
    attributed_to = _as_str(raw.get("attributed_to"))
    if component_type == "plugin" and attributed_to:
        return attributed_to
    source = _component(raw).get("source")
    if isinstance(source, dict):
        purl = _as_str(source.get("purl"))
        if component_type == "package" and purl:
            return purl
        match_coordinate = _as_str(source.get("match_coordinate"))
        if match_coordinate:
            return match_coordinate
    return f"{component_type}/{label}"


def _draft_sort_key(draft: _CardDraft) -> tuple[int, int, int, str]:
    max_severity = max(_SEVERITY_RANK[e.severity] for e in draft.evidence)
    posture_weight = 1 if any(e.finding_type == "posture" for e in draft.evidence) else 0
    confidence = max(_CONFIDENCE_RANK[e.confidence] for e in draft.evidence)
    return (-max_severity, -posture_weight, -confidence, draft.component_label)


def _evidence_sort_key(evidence: TriageEvidence) -> tuple[int, str, str]:
    return (-_SEVERITY_RANK[evidence.severity], evidence.finding_type, evidence.id)


def _draft_priority(draft: _CardDraft) -> Priority:
    return max((e.severity for e in draft.evidence), key=lambda value: _SEVERITY_RANK[value])


def _draft_confidence(draft: _CardDraft) -> Confidence:
    return max((e.confidence for e in draft.evidence), key=lambda value: _CONFIDENCE_RANK[value])


def _draft_action(draft: _CardDraft) -> TriageAction:
    for evidence in draft.evidence:
        if evidence.finding_type == "vulnerability" and evidence.fixed_in:
            return "upgrade"
    for evidence in draft.evidence:
        text = f"{evidence.id} {evidence.title} {evidence.remediation or ''}".lower()
        if any(token in text for token in ("mutable", "unpinned", "@latest", "no digest")):
            return "pin"
        if any(token in text for token in ("insecure transport", "unauthenticated", "http")):
            return "replace"
    if any(e.finding_type == "observation" and e.confidence == "low" for e in draft.evidence):
        return "review"
    if any(e.id.startswith("MAL-") for e in draft.evidence):
        return "remove"
    return "review"


def _why_it_matters(draft: _CardDraft, action: TriageAction) -> str:
    top = sorted(draft.evidence, key=_evidence_sort_key)[0]
    path = _path_label(draft.composition_path)
    source = f" from {top.source}" if top.source else ""
    if top.finding_type == "vulnerability":
        fix = f"; fixed in {top.fixed_in}" if top.fixed_in else ""
        return (
            f"{draft.component_label} is in the agent composition path {path} and has "
            f"{top.severity} advisory evidence {top.id}{source}{fix}."
        )
    if top.finding_type == "posture":
        return (
            f"{draft.component_label} has {top.severity} posture evidence {top.id} "
            f"in the agent composition path {path}."
        )
    return (
        f"{draft.component_label} has {top.severity} observation evidence {top.id}{source} "
        f"in the agent composition path {path}; action is {action}."
    )


def _scope_limits(draft: _CardDraft) -> list[str]:
    limits = [
        "Static composition report; runtime behavior was not observed.",
        "Exposure priority uses scan evidence only and is not proof of exploitability.",
    ]
    if any(item.get("type") == "mcp_server" for item in draft.composition_path):
        limits.append("MCP server internals were not executed during triage.")
    return limits


def _component(raw: dict[str, Any]) -> dict[str, Any]:
    component = raw.get("component")
    return dict(component) if isinstance(component, dict) else {}


def _component_path(raw: dict[str, Any]) -> list[dict[str, str]]:
    path = raw.get("component_path")
    out: list[dict[str, str]] = []
    if isinstance(path, list):
        for item in path:
            if not isinstance(item, dict):
                continue
            typ = _as_str(item.get("type"))
            name = _as_str(item.get("name"))
            if typ and name:
                out.append({"type": typ, "name": name})
    if out:
        return out
    component = _component(raw)
    typ = _as_str(component.get("type")) or "component"
    name = _as_str(component.get("name")) or "<unidentified>"
    return [{"type": typ, "name": name}]


def _active_in(raw: dict[str, Any]) -> list[str]:
    active_in = raw.get("active_in")
    if not isinstance(active_in, list):
        return []
    return sorted({item for item in active_in if isinstance(item, str)})


def _path_label(path: list[dict[str, str]]) -> str:
    return " -> ".join(f"{item['type']} {item['name']}" for item in path) or "<unknown>"


def _priority(value: object) -> Priority:
    normalized = str(value or "info").lower()
    if normalized == "unknown" or normalized == "none":
        return "info"
    if normalized in {"critical", "high", "medium", "low", "info"}:
        return normalized  # type: ignore[return-value]
    return "info"


def _confidence(value: object) -> Confidence:
    normalized = str(value or "low").lower()
    if normalized in {"high", "medium", "low"}:
        return normalized  # type: ignore[return-value]
    return "low"


def _advisory_id(raw: dict[str, Any]) -> str | None:
    matched = raw.get("matched_advisory")
    if not isinstance(matched, dict):
        return None
    return _as_str(matched.get("id"))


def _matched_source(raw: dict[str, Any]) -> str | None:
    matched = raw.get("matched_advisory")
    if not isinstance(matched, dict):
        return None
    return _as_str(matched.get("source"))


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
