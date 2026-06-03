"""Facade re-export: severity normalization. See ADR-0028."""

from tools.severity import derive_severity_label, derive_severity_score

__all__ = ["derive_severity_label", "derive_severity_score"]
