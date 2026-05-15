"""Normalized representation of a detected agent-stack component."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

PURL_ECOSYSTEM_MAP = {
    "npm": "npm",
    "PyPI": "pypi",
    "pypi": "pypi",
    "GitHub": "github",
    "github": "github",
    "Docker": "docker",
    "docker": "docker",
}


def encode_purl_name(name: str) -> str:
    return quote(name, safe="/")


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
    attributed_to: Optional[str] = None
    extra: dict = field(default_factory=dict)
    # Composition classification, set by the repo walker post-parse:
    #   "agent-component"     — agent-stack surface (plugins, MCP servers,
    #                           skills, commands, agents, hooks, settings)
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
        purl_eco = PURL_ECOSYSTEM_MAP.get(self.ecosystem)
        if not purl_eco:
            return None
        encoded = encode_purl_name(self.name)
        if self.version:
            return f"pkg:{purl_eco}/{encoded}@{self.version}"
        return f"pkg:{purl_eco}/{encoded}"
