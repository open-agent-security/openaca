"""Source-attributed scanner/audit observations.

Observation findings are not OpenACA advisory records. They preserve the
scanner or audit source that made the claim so downstream users can calibrate
confidence and policy separately from upstream vulnerabilities and OpenACA
posture rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

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

    @property
    def component_label(self) -> str:
        name = self.component.get("name")
        if isinstance(name, str) and name:
            return name
        return "<unidentified>"

    @property
    def location(self) -> str:
        if self.declared_by is None:
            return ""
        path = self.declared_by.get("path")
        return path if isinstance(path, str) else ""
