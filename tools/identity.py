"""Shared identity and MCP match-coordinate helpers."""

from __future__ import annotations

from dataclasses import dataclass
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
_PACKAGE_FLAGS_BY_LAUNCHER = {
    "npx": frozenset({"--package", "-p"}),
    "uvx": frozenset({"--from"}),
}

_NPX_UVX_FLAGS_WITH_VALUE = frozenset(
    {
        "--package",
        "-p",
        "--from",
        "--with",
        "--python",
        "--call",
        "-c",
    }
)


@dataclass(frozen=True)
class MatchCoordinate:
    kind: str
    purl: str | None = None
    ecosystem: str | None = None
    name: str | None = None
    version: str | None = None
    git_repo: str | None = None
    git_ref: str | None = None
    value: str | None = None


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


def is_package_source_ref(ref: Any) -> bool:
    return bool(ref.name) and purl_type_for_ecosystem(ref.ecosystem) in PACKAGE_SOURCE_PURL_TYPES


def match_coordinate_for_bom(ref: Any) -> str | None:
    candidate = (ref.extra or {}).get("match_coordinate")
    return candidate if isinstance(candidate, str) and candidate else None


def launcher_and_args(tokens: list[str]) -> tuple[str, list[str]]:
    if len(tokens) >= 3 and tokens[:3] == ["uv", "tool", "run"]:
        return "uvx", tokens[3:]
    if not tokens:
        return "", []
    return tokens[0], tokens[1:]


def mcp_package_source(install_source: object) -> tuple[str, str, str] | None:
    if not isinstance(install_source, str):
        return None
    tokens = install_source.split()
    if len(tokens) < 2:
        return None
    launcher, args = launcher_and_args(tokens)
    if launcher not in ("npx", "uvx") or not args:
        return None
    package = _extract_mcp_package_from_args(launcher, args)
    if package is None:
        return None
    ecosystem = "npm" if launcher == "npx" else "PyPI"
    return launcher, ecosystem, package


def is_mcp_package_launch_install_source(install_source: object) -> bool:
    return mcp_package_source(install_source) is not None


def is_unpinned_mcp_package_launch(ref: Any) -> bool:
    if ref.version or not (ref.ecosystem and ref.name):
        return False
    if (ref.extra or {}).get("component_type") != "mcp_server":
        return False
    source = mcp_package_source((ref.extra or {}).get("install_source"))
    if source is None:
        return False
    _launcher, ecosystem, _package = source
    return purl_type_for_ecosystem(ref.ecosystem) == purl_type_for_ecosystem(ecosystem)


def unpinned_mcp_package(ref: Any) -> Optional[tuple[str, str]]:
    source = mcp_package_source((ref.extra or {}).get("install_source"))
    if source is not None and not ref.version:
        _launcher, ecosystem, package = source
        if not ref.ecosystem or purl_type_for_ecosystem(ref.ecosystem) == purl_type_for_ecosystem(
            ecosystem
        ):
            return ecosystem, package
    if is_unpinned_mcp_package_launch(ref):
        assert ref.ecosystem is not None
        assert ref.name is not None
        return ref.ecosystem, ref.name
    return None


def match_coordinates(ref: Any) -> list[MatchCoordinate]:
    unpinned_package = unpinned_mcp_package(ref)
    if unpinned_package is not None:
        ecosystem, name = unpinned_package
        return [MatchCoordinate(kind="package", ecosystem=ecosystem, name=name)]

    if ref.ecosystem and ref.name and ref.version and ref.purl:
        if purl_type_for_ecosystem(ref.ecosystem) in {"npm", "pypi"}:
            return [MatchCoordinate(kind="purl", purl=ref.purl)]
        if ref.ecosystem in {"github", "GitHub"}:
            repo = f"github.com/{ref.name.lower()}"
            return [MatchCoordinate(kind="git_commit", git_repo=repo, git_ref=ref.version)]

    if ref.ecosystem in {"github", "GitHub"} and ref.name:
        git_ref = (ref.extra or {}).get("git_ref")
        if isinstance(git_ref, str) and git_ref:
            repo = f"github.com/{ref.name.lower()}"
            return [MatchCoordinate(kind="git_version", git_repo=repo, git_ref=git_ref)]

    match_coordinate = match_coordinate_for_bom(ref)
    if match_coordinate:
        return [MatchCoordinate(kind="external_audit", value=match_coordinate)]

    return []


def infer_unpinned_mcp_package(extra: dict[str, Any]) -> tuple[str, str] | None:
    if extra.get("component_type") != "mcp_server":
        return None
    source = mcp_package_source(extra.get("install_source"))
    if source is None:
        return None
    _launcher, ecosystem, package = source
    if ecosystem == "npm" and _safe_package_name(package, allow_scope=True):
        return ecosystem, package
    if ecosystem == "PyPI" and _safe_package_name(package, allow_scope=False):
        return ecosystem, package
    return None


def safe_unpinned_mcp_install_source(
    *,
    install_source: object,
) -> str | None:
    source = mcp_package_source(install_source)
    if source is None:
        return None
    launcher, _ecosystem, package = source
    if _ecosystem == "npm" and not _safe_package_name(package, allow_scope=True):
        return None
    if _ecosystem == "PyPI" and not _safe_package_name(package, allow_scope=False):
        return None
    return f"{launcher} {package}"


def canonical_component_identity(ref: Any) -> Optional[str]:
    component_type = (ref.extra or {}).get("component_type")
    if component_type == "mcp_server":
        server_name = _component_path_leaf(ref, "mcp_server")
        if server_name:
            return f"mcp-server/{server_name}"
        if ref.component_identity and ref.component_identity.startswith("mcp-server/"):
            return ref.component_identity
        if ref.name:
            return f"mcp-server/{ref.name}"

    if ref.component_identity:
        return ref.component_identity

    if is_package_source_ref(ref):
        ecosystem = canonical_ecosystem(ref.ecosystem)
        if ecosystem and ref.name:
            return f"package/{ecosystem}/{ref.name}"

    return None


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


def _extract_mcp_package_from_args(launcher: str, args: list[str]) -> str | None:
    package_flags = _PACKAGE_FLAGS_BY_LAUNCHER.get(launcher, frozenset())
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            break
        if token.startswith("-"):
            for flag in package_flags:
                if token.startswith(f"{flag}="):
                    pkg = token[len(flag) + 1 :]
                    if pkg:
                        return pkg
            if token in package_flags and i + 1 < len(args):
                candidate = args[i + 1]
                if not candidate.startswith("-"):
                    return candidate
        i += 1

    skip_next = False
    after_terminator = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if after_terminator:
            return token
        if token == "--":
            after_terminator = True
            continue
        if token.startswith("-"):
            if token in _NPX_UVX_FLAGS_WITH_VALUE and "=" not in token:
                skip_next = True
            continue
        return token
    return None


def _safe_package_name(value: str, *, allow_scope: bool) -> bool:
    if "://" in value or value.startswith(("/", ".")):
        return False
    if allow_scope and value.startswith("@"):
        parts = value.split("/", 1)
        if len(parts) != 2:
            return False
        scope = parts[0][1:]
        name = parts[1]
        return all(
            part.replace(".", "").replace("_", "").replace("-", "").isalnum()
            for part in (scope, name)
        )
    return value.replace(".", "").replace("_", "").replace("-", "").isalnum()


def _component_path_leaf(ref: Any, component_type: str) -> Optional[str]:
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


def _extract_arg_value(args: list[str], flag: str) -> Optional[str]:
    prefix = f"{flag}="
    for index, arg in enumerate(args):
        if arg.startswith(prefix):
            return arg[len(prefix) :]
        if arg == flag and index + 1 < len(args):
            return args[index + 1]
    return None
