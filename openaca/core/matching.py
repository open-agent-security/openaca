"""Facade re-export: advisory matching. See ADR-0028."""

from tools.matcher import Finding, match

__all__ = ["Finding", "match"]
