"""Scanner-emitted posture findings — configuration-hygiene rules that flag
risky install postures without requiring a CVE lookup.

Posture findings are NOT overlay records. They do not mint OpenACA IDs and
do not appear in `overlays/`. They flow through the scanner output pipeline
as a separate list alongside vulnerability findings. See plan 014 and
ADR-0009 for the architectural rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Standards:
    """Standards mapping for a posture finding.

    The taxonomy fields are deliberately separate so consumers can filter
    by any single standard (e.g., "show me all CWE-1357 hits"). Empty lists
    are dropped on serialization to keep JSON/SARIF output uncluttered.
    """

    cwe: list[str] = field(default_factory=list)
    openssf_scorecard: list[str] = field(default_factory=list)
    slsa: list[str] = field(default_factory=list)
    owasp_app_top_10: list[str] = field(default_factory=list)
    owasp_agentic_top10: list[str] = field(default_factory=list)
    owasp_mcp_top10: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {k: v for k, v in self.__dict__.items() if v}


@dataclass(frozen=True)
class PostureFinding:
    rule_id: str
    title: str
    severity: Severity
    confidence: Confidence
    component: dict[str, Any]
    active_in: list[str]
    declared_by: dict[str, Any] | None
    component_path: list[dict[str, str]]
    standards: Standards
    remediation: str
    finding_type: str = "posture"

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
