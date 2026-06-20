"""Normalized representation of a detected agent component."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from tools.identity import (
    canonical_component_identity,
    canonical_ecosystem,
    encode_purl_name,
    is_package_source_ref,
    is_unpinned_mcp_package_launch,
    purl_type_for_ecosystem,
    safe_pinned_mcp_install_source,
    unpinned_mcp_package,
)

__all__ = [
    "ComponentRef",
    "canonical_component_identity",
    "canonical_ecosystem",
    "encode_purl_name",
    "is_package_source_ref",
    "is_unpinned_mcp_package_launch",
    "purl_type_for_ecosystem",
    "safe_pinned_mcp_install_source",
    "unpinned_mcp_package",
]


@dataclass(frozen=True)
class ComponentRef:
    """A single component installation discovered in a repository.

    Either (ecosystem + name + version) is set with a derivable standard PURL,
    or component_identity is set with an OpenACA-native identifier.
    """

    ecosystem: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
    source_manifest: str = ""
    source_locator: str = ""
    component_identity: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Composition classification, set by the repo walker post-parse:
    #   "agent-component"     — first-class agent surface (plugins, MCP
    #                           servers, skills, commands, agents, hooks,
    #                           settings)
    #   "agent-dependency"    — software dep co-located with a plugin
    #                           manifest (.claude-plugin/plugin.json sibling)
    #   "software-dependency" — software dep with no plugin co-location;
    #                           suppressed from output in V0 (out of scope
    #                           for agent-composition analysis)
    # Endpoint refs are always agent-component or agent-dependency; the
    # filter at scan-time drops software-dependency refs before matching,
    # federation, and rendering.
    scope: str = "agent-component"

    @property
    def purl(self) -> Optional[str]:
        if not (self.ecosystem and self.name):
            return None
        purl_eco = purl_type_for_ecosystem(self.ecosystem)
        if not purl_eco:
            return None
        encoded = encode_purl_name(self.name)
        if self.version:
            return f"pkg:{purl_eco}/{encoded}@{self.version}"
        return f"pkg:{purl_eco}/{encoded}"
