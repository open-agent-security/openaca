"""Normalized representation of a detected agent component."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
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
PACKAGE_SOURCE_PURL_TYPES = frozenset({"npm", "pypi", "github", "docker"})


def encode_purl_name(name: str) -> str:
    return quote(name, safe="/")


def canonical_ecosystem(ecosystem: object) -> Optional[str]:
    if not isinstance(ecosystem, str):
        return None
    return PURL_ECOSYSTEM_MAP.get(ecosystem) or ecosystem.lower()


def purl_type_for_ecosystem(ecosystem: object) -> Optional[str]:
    if not isinstance(ecosystem, str):
        return None
    return PURL_ECOSYSTEM_MAP.get(ecosystem)


def purl_type(purl: object) -> Optional[str]:
    if not isinstance(purl, str) or not purl.startswith("pkg:"):
        return None
    body = purl[4:]
    if "/" not in body:
        return None
    value, _remainder = body.split("/", 1)
    return value or None


def is_package_source_ref(ref: "ComponentRef") -> bool:
    return bool(ref.name) and purl_type_for_ecosystem(ref.ecosystem) in PACKAGE_SOURCE_PURL_TYPES


def canonical_component_identity(ref: "ComponentRef") -> Optional[str]:
    """Return the OpenACA agent-graph occurrence identity for a component.

    `ComponentRef.component_identity` predates ADR-0029 and can still carry
    source-shaped MCP identities such as `mcp-remote/...`. This helper is the
    boundary for the canonical `openaca:identity` value emitted into BOMs and
    Fleet posture payloads.
    """
    component_type = (ref.extra or {}).get("component_type")
    if component_type == "mcp_server":
        server_name = _component_path_leaf(ref, "mcp_server")
        if server_name:
            return f"mcp-server/{server_name}"
        if ref.component_identity and ref.component_identity.startswith("mcp-server/"):
            return ref.component_identity
        if ref.name:
            return f"mcp-server/{ref.name}"

    if is_package_source_ref(ref) and ref.attributed_to:
        parent = _without_observed_version(ref.attributed_to)
        ecosystem = canonical_ecosystem(ref.ecosystem)
        if parent and ecosystem and ref.name:
            return f"{parent}/deps/{ecosystem}/{ref.name}"

    if ref.component_identity:
        return ref.component_identity

    if is_package_source_ref(ref):
        ecosystem = canonical_ecosystem(ref.ecosystem)
        if ecosystem and ref.name:
            return f"package/{ecosystem}/{ref.name}"

    return None


def _component_path_leaf(ref: "ComponentRef", component_type: str) -> Optional[str]:
    component_path = (ref.extra or {}).get("component_path")
    if not isinstance(component_path, list):
        return None
    for item in reversed(component_path):
        if not isinstance(item, dict):
            continue
        if item.get("type") != component_type:
            continue
        name = item.get("name")
        if isinstance(name, str) and name:
            return name
    return None


def _without_observed_version(identity: str) -> str:
    if identity.startswith("plugin/") and "@" in identity.rsplit("/", 1)[-1]:
        return identity.rsplit("@", 1)[0]
    return identity


def safe_pinned_mcp_install_source(
    *,
    launcher: str,
    purl: object,
    name: object,
    version: object,
    install_source: object = None,
    source_subdirectory: object = None,
) -> Optional[str]:
    if not (isinstance(launcher, str) and launcher and isinstance(name, str) and name):
        return None
    subdirectory = source_subdirectory if isinstance(source_subdirectory, str) else None
    package_type = purl_type(purl)
    if package_type == "github":
        source = f"git+https://github.com/{name}"
        if isinstance(version, str) and version:
            source = f"{source}@{version}"
            if subdirectory is not None:
                source = f"{source}#subdirectory={subdirectory}"
            return f"{launcher} {source}"
        raw_from = _extract_arg_value(str(install_source).split(), "--from")
        prefix = f"git+https://github.com/{name}"
        if raw_from is not None and raw_from.startswith(prefix):
            suffix = raw_from[len(prefix) :]
            if suffix.startswith(".git"):
                suffix = suffix[len(".git") :]
            if suffix.startswith("@"):
                source = f"{source}{suffix.split('#', 1)[0]}"
        if subdirectory is not None:
            source = f"{source}#subdirectory={subdirectory}"
        return f"{launcher} {source}"
    if not (isinstance(version, str) and version):
        return None
    if package_type == "npm":
        return f"{launcher} {name}@{version}"
    if package_type == "pypi":
        return f"{launcher} {name}=={version}"
    if package_type == "docker":
        sep = "@" if version.startswith("sha256:") else ":"
        return f"{launcher} {name}{sep}{version}"
    return None


def _extract_arg_value(args: list[str], flag: str) -> Optional[str]:
    prefix = f"{flag}="
    for index, arg in enumerate(args):
        if arg.startswith(prefix):
            return arg[len(prefix) :]
        if arg == flag and index + 1 < len(args):
            return args[index + 1]
    return None


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
