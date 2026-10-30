"""Source-attributed scanner/audit observations.

Observation findings are not OpenACA advisory records. They preserve the
scanner or audit source that made the claim so downstream users can calibrate
confidence and policy separately from upstream vulnerabilities and OpenACA
posture rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from tools.posture.finding import _component_label, _location

Severity = Literal["info", "low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class ObservationFinding:
    source: str
    source_version: str
    observation_id: str
    title: str
    severity: Severity
    confidence: Confidence
    component: dict[str, Any]
    subject_coordinate: str
    evidence: dict[str, Any] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    remediation: str | None = None
    declared_by: dict[str, Any] | None = None
    component_path: list[dict[str, str]] = field(default_factory=list)
    finding_type: str = "observation"
    bom_ref: str | None = None
    # The agent this row belongs to (ADR-0044), mirroring `matcher.Finding`.
    agent_kind: str | None = None
    agent_id: str | None = None

    @property
    def component_label(self) -> str:
        return _component_label(self.component)

    @property
    def location(self) -> str:
        return _location(self.declared_by)
