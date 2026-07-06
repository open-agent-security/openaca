"""Closed capability taxonomy and the Capability evidence record."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Capability", "CAPABILITY_NAMES", "COVERAGE_LEVELS"]

CAPABILITY_NAMES = frozenset(
    {
        "file_read",
        "file_write",
        "shell_exec",
        "network_egress",
        "credential_access",
        "sensitive_data_access",
    }
)

COVERAGE_LEVELS = ("unknown", "partial", "complete")

_EXECUTION_LOCI = frozenset({"local", "remote"})
_METHODS = frozenset({"declared", "curated", "inferred"})
_CONFIDENCES = frozenset({"high", "medium", "low"})


@dataclass(frozen=True)
class Capability:
    name: str
    execution_locus: str
    method: str
    source: str
    source_version: str
    confidence: str
    evidence: Sequence[dict[str, Any]] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.name not in CAPABILITY_NAMES:
            raise ValueError(f"unknown capability name: {self.name!r}")
        if self.execution_locus not in _EXECUTION_LOCI:
            raise ValueError(f"invalid execution_locus: {self.execution_locus!r}")
        if self.method not in _METHODS:
            raise ValueError(f"invalid method: {self.method!r}")
        if self.confidence not in _CONFIDENCES:
            raise ValueError(f"invalid confidence: {self.confidence!r}")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not self.evidence:
            raise ValueError("capability evidence must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "execution_locus": self.execution_locus,
            "method": self.method,
            "source": self.source,
            "source_version": self.source_version,
            "confidence": self.confidence,
            "evidence": [dict(e) for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Capability:
        return cls(
            name=data["name"],
            execution_locus=data["execution_locus"],
            method=data["method"],
            source=data["source"],
            source_version=data["source_version"],
            confidence=data["confidence"],
            evidence=tuple(data["evidence"]),
        )
