"""Closed capability taxonomy and the Capability evidence record."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

__all__ = ["Capability", "CAPABILITY_NAMES", "COVERAGE_LEVELS", "capabilities_for_ref"]

if TYPE_CHECKING:
    from tools.capability_corpus import CapabilityCorpus
    from tools.component_ref import ComponentRef

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
        for e in self.evidence:
            if not isinstance(e, dict):
                raise ValueError(f"capability evidence entries must be objects, got {e!r}")
            if not e.get("kind"):
                raise ValueError(f"capability evidence entries must have a 'kind', got {e!r}")

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


def capabilities_for_ref(
    ref: ComponentRef, corpus: CapabilityCorpus
) -> tuple[list[Capability], str]:
    # Function-local imports break the import cycle: capability_extract already
    # imports Capability from this module, so importing it at module top would
    # cycle.
    from tools.capability_extract import declared_capabilities
    from tools.identity import (
        _safe_package_name,
        canonical_component_identity,
        mcp_package_source,
        normalize_launcher_command,
        strip_package_version,
    )
    from tools.mcp_launch_resolve import normalize_pypi_name

    coordinate: str | None = None
    install_source = (ref.extra or {}).get("install_source")
    if isinstance(install_source, str):
        install_source = normalize_launcher_command(install_source)
    source = mcp_package_source(install_source)
    if source is not None:
        _launcher, ecosystem, package = source
        stripped = strip_package_version(ecosystem, package)
        if ecosystem == "PyPI":
            # PyPI project names are case- and separator-insensitive (PEP 503);
            # curated coordinates are keyed on the normalized name, so a launch
            # spelled `AWS_MCP_Server` must match `PyPI/aws-mcp-server`.
            stripped = normalize_pypi_name(stripped)
        allow_scope = ecosystem == "npm"
        if _safe_package_name(stripped, allow_scope=allow_scope):
            coordinate = f"{ecosystem}/{stripped}"

    identity = canonical_component_identity(ref)
    declared = declared_capabilities(ref)
    curated = corpus.lookup(identity or "", match_coordinate=coordinate)

    merged: dict[tuple[str, str], Capability] = {}
    for cap in curated:
        merged[(cap.name, cap.execution_locus)] = cap
    for cap in declared:
        merged[(cap.name, cap.execution_locus)] = cap

    caps = list(merged.values())
    coverage = "unknown" if not caps else "partial"
    return caps, coverage
