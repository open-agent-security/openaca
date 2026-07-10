"""Component-centric exposure decisions over structured OpenACA scan JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Priority = Literal["critical", "high", "medium", "low", "info"]
Confidence = Literal["high", "medium", "low"]
ExposureAction = Literal["remove", "pin", "upgrade", "approve", "replace", "accept", "review"]

_AGENT_COMPONENT_TYPES = {"plugin", "mcp_server", "skill", "hook", "command", "agent"}
_SEVERITY_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class ExposurePathNode:
    type: str
    name: str
    bom_ref: str
    identity: str | None = None
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.type,
            "name": self.name,
            "bom_ref": self.bom_ref,
            "identity": self.identity,
        }
        if self.version is not None:
            out["version"] = self.version
        return out


@dataclass(frozen=True)
class ExposureComponent:
    identity: str | None
    type: str
    name: str
    versions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "type": self.type,
            "name": self.name,
            "versions": self.versions,
        }


@dataclass(frozen=True)
class ExposureOccurrence:
    bom_ref: str
    composition_paths: list[list[ExposurePathNode]]
    active_in: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bom_ref": self.bom_ref,
            "composition_paths": [
                [node.to_dict() for node in path] for path in self.composition_paths
            ],
            "active_in": self.active_in,
        }


@dataclass(frozen=True)
class ExposureEvidence:
    finding_type: str
    id: str
    title: str
    severity: Priority
    confidence: Confidence
    provenance: str
    bom_ref: str
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
            "bom_ref": self.bom_ref,
            "component": self.component,
        }
        if self.source is not None:
            out["source"] = self.source
        if self.fixed_in is not None:
            out["fixed_in"] = self.fixed_in
        if self.remediation is not None:
            out["remediation"] = self.remediation
        return out


@dataclass(frozen=True)
class ExposureDecision:
    priority: Priority
    confidence: Confidence
    action: ExposureAction
    why_it_matters: str
    scope_limits: list[str]


@dataclass(frozen=True)
class ExposureCard:
    component: ExposureComponent
    occurrences: list[ExposureOccurrence]
    rank: int
    priority: Priority
    confidence: Confidence
    action: ExposureAction
    evidence: list[ExposureEvidence]
    why_it_matters: str
    scope_limits: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component.to_dict(),
            "occurrences": [item.to_dict() for item in self.occurrences],
            "rank": self.rank,
            "priority": self.priority,
            "confidence": self.confidence,
            "action": self.action,
            "evidence": [item.to_dict() for item in self.evidence],
            "why_it_matters": self.why_it_matters,
            "scope_limits": self.scope_limits,
        }


@dataclass
class _OccurrenceDraft:
    bom_ref: str
    paths: dict[str, list[ExposurePathNode]] = field(default_factory=dict)
    active_in: set[str] = field(default_factory=set)


@dataclass
class _CardDraft:
    identity: str | None
    component_type: str
    names: set[str] = field(default_factory=set)
    versions: set[str] = field(default_factory=set)
    occurrences: dict[str, _OccurrenceDraft] = field(default_factory=dict)
    evidence: dict[tuple[str, str, str], ExposureEvidence] = field(default_factory=dict)


def build_exposure_cards(scan_doc: dict[str, Any]) -> list[ExposureCard]:
    """Build deterministic exposure decisions from structured scan evidence."""
    findings = scan_doc.get("findings")
    if not isinstance(findings, list):
        raise ValueError("scan JSON must contain findings[]")

    grouped: dict[tuple[str, str], _CardDraft] = {}
    for raw in findings:
        if not isinstance(raw, dict):
            continue
        evidence = _evidence_from_finding(raw)
        if evidence is None:
            continue
        path = _component_path(raw)
        selected = _select_component(path)
        group_key = (
            ("identity", selected.identity)
            if selected.identity is not None
            else ("occurrence", selected.bom_ref)
        )
        draft = grouped.setdefault(
            group_key,
            _CardDraft(identity=selected.identity, component_type=selected.type),
        )
        draft.names.add(selected.name)
        if selected.version is not None:
            draft.versions.add(selected.version)
        occurrence = draft.occurrences.setdefault(
            selected.bom_ref,
            _OccurrenceDraft(bom_ref=selected.bom_ref),
        )
        occurrence.paths[_path_key(path)] = path
        occurrence.active_in.update(_active_in(raw))
        draft.evidence[(evidence.finding_type, evidence.id, evidence.bom_ref)] = evidence

    drafts = sorted(grouped.values(), key=_draft_sort_key)
    cards: list[ExposureCard] = []
    for rank, draft in enumerate(drafts, start=1):
        evidence = sorted(draft.evidence.values(), key=_evidence_sort_key)
        component = ExposureComponent(
            identity=draft.identity,
            type=draft.component_type,
            name=sorted(draft.names)[0],
            versions=sorted(draft.versions),
        )
        occurrences = _occurrences(draft)
        decision = decide_exposure(
            component,
            evidence,
            [path for occurrence in occurrences for path in occurrence.composition_paths],
        )
        cards.append(
            ExposureCard(
                component=component,
                occurrences=occurrences,
                rank=rank,
                priority=decision.priority,
                confidence=decision.confidence,
                action=decision.action,
                evidence=evidence,
                why_it_matters=decision.why_it_matters,
                scope_limits=decision.scope_limits,
            )
        )
    return cards


def decide_exposure(
    component: ExposureComponent,
    evidence: list[ExposureEvidence],
    composition_paths: list[list[ExposurePathNode]],
) -> ExposureDecision:
    """Compute shared decision fields without imposing an occurrence namespace."""
    if not evidence:
        raise ValueError("exposure decision requires evidence")
    if not composition_paths:
        raise ValueError("exposure decision requires a composition path")
    ordered_evidence = sorted(evidence, key=_evidence_sort_key)
    ordered_paths = sorted(composition_paths, key=_path_key)
    action = _draft_action(ordered_evidence)
    return ExposureDecision(
        priority=_draft_priority(ordered_evidence),
        confidence=_draft_confidence(ordered_evidence),
        action=action,
        why_it_matters=_why_it_matters(component, ordered_paths[0], ordered_evidence, action),
        scope_limits=_scope_limits(ordered_paths),
    )


def _evidence_from_finding(raw: dict[str, Any]) -> ExposureEvidence | None:
    finding_type = _as_str(raw.get("finding_type")) or "finding"
    bom_ref = _as_str(raw.get("bom_ref"))
    if bom_ref is None:
        if finding_type == "posture" and _as_str(_component(raw).get("type")) == "agent_config":
            return None
        raise ValueError("component-scoped finding must contain bom_ref")
    if finding_type == "vulnerability":
        evidence_id = _as_str(raw.get("id")) or _advisory_id(raw) or "unknown-advisory"
        return ExposureEvidence(
            finding_type=finding_type,
            id=evidence_id,
            title=_as_str(raw.get("summary")) or _as_str(raw.get("title")) or evidence_id,
            severity=_priority(raw.get("severity")),
            confidence=_confidence(raw.get("confidence")),
            provenance="advisory-derived",
            bom_ref=bom_ref,
            source=_as_str(raw.get("source")) or _matched_source(raw),
            fixed_in=_as_str(raw.get("fixed_in")),
            component=_component(raw),
        )
    if finding_type == "posture":
        evidence_id = _as_str(raw.get("rule_id")) or "unknown-posture"
        source = _as_str(raw.get("source")) or "openaca"
        return ExposureEvidence(
            finding_type=finding_type,
            id=evidence_id,
            title=_as_str(raw.get("title")) or evidence_id,
            severity=_priority(raw.get("severity")),
            confidence=_confidence(raw.get("confidence")),
            provenance=_scanner_provenance(source),
            bom_ref=bom_ref,
            source=source,
            remediation=_as_str(raw.get("remediation")),
            component=_component(raw),
        )
    if finding_type == "observation":
        evidence_id = _as_str(raw.get("observation_id")) or "unknown-observation"
        source = _as_str(raw.get("source")) or "unknown"
        return ExposureEvidence(
            finding_type=finding_type,
            id=evidence_id,
            title=_as_str(raw.get("title")) or evidence_id,
            severity=_priority(raw.get("severity")),
            confidence=_confidence(raw.get("confidence")),
            provenance=_scanner_provenance(source),
            bom_ref=bom_ref,
            source=source,
            remediation=_as_str(raw.get("remediation")),
            component=_component(raw),
        )
    return None


def _scanner_provenance(source: str) -> str:
    return "scanner-derived" if source == "openaca" else "external-scanner-derived"


def _component_path(raw: dict[str, Any]) -> list[ExposurePathNode]:
    component = _component(raw)
    own_type = _as_str(component.get("type")) or "component"
    own_name = _as_str(component.get("name")) or "<unidentified>"
    own_bom_ref = _as_str(raw.get("bom_ref"))
    own_identity = _as_str(component.get("identity"))
    source = component.get("source")
    own_version = _as_str(source.get("version")) if isinstance(source, dict) else None
    raw_path = raw.get("component_path")
    path: list[ExposurePathNode] = []
    if isinstance(raw_path, list):
        for index, item in enumerate(raw_path):
            if not isinstance(item, dict):
                continue
            component_type = _as_str(item.get("type"))
            name = _as_str(item.get("name"))
            if component_type is None or name is None:
                continue
            is_own = index == len(raw_path) - 1 and component_type == own_type and name == own_name
            bom_ref = _as_str(item.get("bom_ref")) or (own_bom_ref if is_own else None)
            if bom_ref is None:
                raise ValueError("every component_path node must contain bom_ref")
            path.append(
                ExposurePathNode(
                    type=component_type,
                    name=name,
                    bom_ref=bom_ref,
                    identity=_as_str(item.get("identity")) or (own_identity if is_own else None),
                    version=_as_str(item.get("version")) or (own_version if is_own else None),
                )
            )
    if path:
        return path
    if own_bom_ref is None:
        raise ValueError("component-scoped finding must contain bom_ref")
    return [
        ExposurePathNode(
            type=own_type,
            name=own_name,
            bom_ref=own_bom_ref,
            identity=own_identity,
            version=own_version,
        )
    ]


def _select_component(path: list[ExposurePathNode]) -> ExposurePathNode:
    for node in path:
        if node.type in _AGENT_COMPONENT_TYPES:
            return node
    return path[-1]


def _path_key(path: list[ExposurePathNode]) -> str:
    return json.dumps([item.to_dict() for item in path], sort_keys=True, separators=(",", ":"))


def _occurrences(draft: _CardDraft) -> list[ExposureOccurrence]:
    return [
        ExposureOccurrence(
            bom_ref=item.bom_ref,
            composition_paths=[item.paths[key] for key in sorted(item.paths)],
            active_in=sorted(item.active_in),
        )
        for item in sorted(draft.occurrences.values(), key=lambda value: value.bom_ref)
    ]


def _draft_sort_key(draft: _CardDraft) -> tuple[int, int, int, str, str]:
    evidence = list(draft.evidence.values())
    max_severity = max(_SEVERITY_RANK[item.severity] for item in evidence)
    posture_weight = int(any(item.finding_type == "posture" for item in evidence))
    confidence = max(_CONFIDENCE_RANK[item.confidence] for item in evidence)
    return (
        -max_severity,
        -posture_weight,
        -confidence,
        sorted(draft.names)[0],
        draft.identity or sorted(draft.occurrences)[0],
    )


def _evidence_sort_key(evidence: ExposureEvidence) -> tuple[int, str, str, str]:
    return (
        -_SEVERITY_RANK[evidence.severity],
        evidence.finding_type,
        evidence.id,
        evidence.bom_ref,
    )


def _draft_priority(evidence: list[ExposureEvidence]) -> Priority:
    return max((item.severity for item in evidence), key=lambda value: _SEVERITY_RANK[value])


def _draft_confidence(evidence: list[ExposureEvidence]) -> Confidence:
    return max((item.confidence for item in evidence), key=lambda value: _CONFIDENCE_RANK[value])


def _draft_action(evidence: list[ExposureEvidence]) -> ExposureAction:
    if any(item.id.startswith("MAL-") for item in evidence):
        return "remove"
    for item in evidence:
        text = f"{item.id} {item.title} {item.remediation or ''}".lower()
        if any(token in text for token in ("mutable", "unpinned", "@latest", "no digest")):
            return "pin"
    for item in evidence:
        text = f"{item.id} {item.title} {item.remediation or ''}".lower()
        if any(token in text for token in ("insecure transport", "unauthenticated", "http")):
            return "replace"
    if any(item.finding_type == "vulnerability" and item.fixed_in for item in evidence):
        return "upgrade"
    return "review"


def _why_it_matters(
    component: ExposureComponent,
    path: list[ExposurePathNode],
    evidence: list[ExposureEvidence],
    action: ExposureAction,
) -> str:
    top = evidence[0]
    path_label = _path_label(path)
    source = f" from {top.source}" if top.source else ""
    if top.finding_type == "vulnerability":
        fix = f"; fixed in {top.fixed_in}" if top.fixed_in else ""
        return (
            f"{component.name} is in the agent composition path {path_label} and has "
            f"{top.severity} advisory evidence {top.id}{source}{fix}."
        )
    if top.finding_type == "posture":
        return (
            f"{component.name} has {top.severity} posture evidence {top.id} "
            f"in the agent composition path {path_label}."
        )
    return (
        f"{component.name} has {top.severity} observation evidence {top.id}{source} "
        f"in the agent composition path {path_label}; action is {action}."
    )


def _scope_limits(composition_paths: list[list[ExposurePathNode]]) -> list[str]:
    limits = [
        "Static composition report; runtime behavior was not observed.",
        "Exposure priority uses scan evidence only and is not proof of exploitability.",
    ]
    if any(node.type == "mcp_server" for path in composition_paths for node in path):
        limits.append("MCP server internals were not executed during triage.")
    return limits


def _component(raw: dict[str, Any]) -> dict[str, Any]:
    component = raw.get("component")
    return dict(component) if isinstance(component, dict) else {}


def _active_in(raw: dict[str, Any]) -> list[str]:
    active_in = raw.get("active_in")
    if not isinstance(active_in, list):
        return []
    return sorted({item for item in active_in if isinstance(item, str)})


def _path_label(path: list[ExposurePathNode]) -> str:
    return " -> ".join(f"{item.type} {item.name}" for item in path) or "<unknown>"


def _priority(value: object) -> Priority:
    normalized = str(value or "info").lower()
    if normalized in {"unknown", "none"}:
        return "info"
    if normalized in _SEVERITY_RANK:
        return normalized  # type: ignore[return-value]
    return "info"


def _confidence(value: object) -> Confidence:
    normalized = str(value or "low").lower()
    if normalized in _CONFIDENCE_RANK:
        return normalized  # type: ignore[return-value]
    return "low"


def _advisory_id(raw: dict[str, Any]) -> str | None:
    matched = raw.get("matched_advisory")
    return _as_str(matched.get("id")) if isinstance(matched, dict) else None


def _matched_source(raw: dict[str, Any]) -> str | None:
    matched = raw.get("matched_advisory")
    return _as_str(matched.get("source")) if isinstance(matched, dict) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
