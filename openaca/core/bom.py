"""Facade re-export: Agent BOM build/parse. See ADR-0028."""

from tools.bom import (
    BOMComponent,
    bom_components_from_cyclonedx,
    build_agent_bom,
    component_refs_from_cyclonedx,
)

__all__ = [
    "BOMComponent",
    "bom_components_from_cyclonedx",
    "build_agent_bom",
    "component_refs_from_cyclonedx",
]
