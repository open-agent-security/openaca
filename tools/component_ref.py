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
    or component_identity is set with an ASVE-native identifier.
    """

    ecosystem: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
    source_manifest: str = ""
    source_locator: str = ""
    component_identity: Optional[str] = None
    extra: dict = field(default_factory=dict)

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
