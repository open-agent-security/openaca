"""Shared identity and MCP match-coordinate helpers."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path
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
    "bunx": frozenset({"--package", "-p"}),
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


# uv global options that consume a separate value token.
# Best-effort against the uv CLI as of mid-2026; missing entries cause
# `uv <unknown-flag> <value> tool run X` to fall back instead of dispatching
# as uvx — false negative, not false positive.  Mirrors _UV_VALUE_FLAGS in
# mcp_json.py (which handles the separate command+args form at parse time).
_UV_GLOBAL_VALUE_FLAGS = frozenset(
    {
        "--directory",
        "--project",
        "--config-file",
        "--cache-dir",
        "--python",
        "--python-preference",
        "--color",
        "--default-index",
        "--index",
        "--index-url",
        "--extra-index-url",
        "--find-links",
        "--keyring-provider",
        "--allow-insecure-host",
        "--trusted-host",
    }
)


def launcher_and_args(tokens: list[str]) -> tuple[str, list[str]]:
    if not tokens:
        return "", []
    if tokens[0] == "uv":
        # Walk past leading uv global options to find the actual subcommand,
        # so `uv --offline tool run pkg` dispatches as uvx. Inline
        # `--flag=value` tokens are consumed in one step (value is embedded).
        i = 1
        while i < len(tokens) and tokens[i].startswith("-"):
            if "=" in tokens[i]:
                i += 1
            elif tokens[i] in _UV_GLOBAL_VALUE_FLAGS:
                i += 2
            else:
                i += 1
        if i + 1 < len(tokens) and tokens[i] == "tool" and tokens[i + 1] == "run":
            return "uvx", tokens[i + 2 :]
    if len(tokens) >= 2 and tokens[0] == "bun" and tokens[1] == "x":
        # `bun x <pkg>` is the spaced form of `bunx <pkg>`.
        return "bunx", tokens[2:]
    return tokens[0], tokens[1:]


def mcp_package_source(install_source: object) -> tuple[str, str, str] | None:
    if not isinstance(install_source, str):
        return None
    tokens = install_source.split()
    if len(tokens) < 2:
        return None
    launcher, args = launcher_and_args(tokens)
    if launcher not in ("npx", "uvx", "bunx") or not args:
        return None
    package = _extract_mcp_package_from_args(launcher, args)
    if package is None:
        return None
    ecosystem = "npm" if launcher in ("npx", "bunx") else "PyPI"
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


_LAUNCHER_EXECUTABLE_EXTENSIONS = frozenset({"cmd", "exe", "bat", "ps1", "com"})


def normalize_launcher_command(src: str) -> str:
    """Reduce a full-path launcher to its bare command name so
    `mcp_package_source` (which matches the launcher token exactly) recognizes
    it — e.g. `/usr/local/bin/npx @scope/pkg` and `npx.cmd @scope/pkg` both ->
    `npx @scope/pkg`. Only a *known Windows executable extension* is stripped;
    a dotted local wrapper like `npx.sh` keeps its name so it is NOT mistaken
    for the `npx` launcher (treating it as npx would attach a curated package's
    capabilities to an unrelated wrapper — the false descriptor ADR-0041
    forbids). Shell-aware so a quoted launcher path tokenizes cleanly; falls
    back to a naive split on bad syntax.
    """
    try:
        tokens = shlex.split(src)
    except ValueError:
        tokens = src.split()
    if not tokens:
        return src
    launcher = Path(tokens[0]).name
    base, dot, ext = launcher.rpartition(".")
    if dot and ext.lower() in _LAUNCHER_EXECUTABLE_EXTENSIONS:
        launcher = base
    return " ".join([launcher, *tokens[1:]])


def strip_package_version(ecosystem: object, package: str) -> str:
    if ecosystem == "npm":
        alias_marker = "@npm:"
        alias_idx = package.find(alias_marker)
        if alias_idx != -1:
            # npm alias spec (`<alias>@npm:<real-name>[@version]`, e.g. from
            # `npx <alias>@npm:<real-name>`): <alias> is a user-chosen label
            # npm/npx accept for any package, so it is exactly the kind of
            # untrusted local name ADR-0041 already treats as unreliable.
            # Resolve to the real package name that actually gets installed
            # and run, not the alias.
            package = package[alias_idx + len(alias_marker) :]
        if package.startswith("@"):
            # A leading @scope/ is part of the name; the pin is a later @.
            scope, sep, rest = package.partition("/")
            if not sep:
                return package
            rest = rest.split("@", 1)[0]
            return f"{scope}/{rest}"
        return package.split("@", 1)[0]
    if ecosystem == "PyPI":
        # Strip PEP 508 extras (`[cli]`), env markers (`;`), and PEP 440 version
        # specifiers (`==`, `>=`, `<`, `~=`, `!=`, `,`) so `fastmcp[cli]>=2` and
        # `fastmcp==2.0` both reduce to `fastmcp` — parity with strip_launch_version.
        marker = re.search(r"[(\[;]|[><=!~]", package)
        if marker:
            package = package[: marker.start()]
    return package.split("@", 1)[0]


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


def canonical_component_identity(
    ref: Any,
    *,
    parent_identity: str | None = None,
) -> Optional[str]:
    extra = ref.extra or {}
    if extra.get("_identity_finalized") is True:
        return ref.component_identity

    component_type = (ref.extra or {}).get("component_type")
    if component_type == "mcp_server":
        package_identity = _role_qualified_package_identity("mcp-server", ref)
        if package_identity:
            return package_identity
        if ref.component_identity and ref.component_identity.startswith("mcp-remote/"):
            return ref.component_identity
        private_identity = _plugin_private_identity(ref, "mcp-server", parent_identity)
        if private_identity:
            return private_identity
        return None

    if component_type == "plugin":
        marketplace = extra.get("marketplace")
        if isinstance(marketplace, str) and marketplace and ref.name:
            return f"plugin/{marketplace}/{ref.name}"
        if isinstance(ref.component_identity, str) and ref.component_identity.count("/") >= 2:
            return ref.component_identity
        return None

    if component_type == "skill":
        external_identity = _external_identity("skill", match_coordinate_for_bom(ref))
        if external_identity:
            return external_identity
        provenance_identity = _skill_provenance_identity(ref)
        if provenance_identity:
            return provenance_identity
        return _plugin_private_identity(ref, "skill", parent_identity)

    if component_type in {"command", "agent", "hook"}:
        return _plugin_private_identity(ref, str(component_type), parent_identity)

    if is_package_source_ref(ref):
        return _role_qualified_package_identity("package", ref)

    return None


def finalize_component_identity(
    ref: Any,
    *,
    parent_identity: str | None = None,
) -> Any:
    """Freeze the source-stable identity selected for one graph node.

    Parsers retain source facts and display aliases. Graph construction owns
    containment, so this is the first layer that can decide whether a
    source-less child belongs to a plugin's private namespace.
    """
    identity = canonical_component_identity(ref, parent_identity=parent_identity)
    namespace = identity if identity and identity.startswith("plugin/") else parent_identity
    extra = {**(ref.extra or {}), "_identity_finalized": True}
    if namespace:
        extra["_identity_namespace"] = namespace
    return replace(
        ref,
        component_identity=identity,
        extra=extra,
    )


def _role_qualified_package_identity(role: str, ref: Any) -> str | None:
    install_source = (ref.extra or {}).get("install_source")
    if isinstance(install_source, str):
        install_source = normalize_launcher_command(install_source)
    source = mcp_package_source(install_source)
    normalized_name = None
    ecosystem = None
    if source is not None and (
        not ref.ecosystem
        or purl_type_for_ecosystem(ref.ecosystem) == purl_type_for_ecosystem(source[1])
    ):
        _launcher, ecosystem, package = source
        normalized_name = strip_package_version(ecosystem, package)
        if not _safe_package_name(normalized_name, allow_scope=ecosystem == "npm"):
            # Not a registry package name (git URL, tarball URL, local folder —
            # npm/uv both accept these as install specs). Fall back to the
            # parser's own ref facts rather than minting a fake package identity.
            normalized_name = None
    if normalized_name is None:
        if is_package_source_ref(ref):
            ecosystem = ref.ecosystem
            normalized_name = strip_package_version(ecosystem, ref.name)
        else:
            return None
    canonical = canonical_ecosystem(ecosystem)
    if not canonical or not normalized_name:
        return None
    if canonical == "pypi":
        normalized_name = re.sub(r"[-_.]+", "-", normalized_name).lower()
    return f"{role}/{canonical}/{normalized_name}"


def _external_identity(role: str, coordinate: str | None) -> str | None:
    if not coordinate:
        return None
    namespace, separator, value = coordinate.partition(":")
    if separator and namespace and value:
        return f"{role}/{namespace}/{value.lstrip('/')}"
    encoded = quote(coordinate, safe="/@._-")
    return f"{role}/external/{encoded}" if encoded else None


def _skill_provenance_identity(ref: Any) -> str | None:
    provenance = (ref.extra or {}).get("source_provenance")
    if not isinstance(provenance, dict) or provenance.get("status") != "known":
        return None
    source_type = provenance.get("source_type")
    source = provenance.get("source")
    if not isinstance(source_type, str) or not isinstance(source, str) or not source:
        return None
    if source_type.lower() != "github":
        return None
    source = _normalize_github_source(source)
    if not source:
        return None
    skill_path = provenance.get("skill_path")
    leaf = skill_path if isinstance(skill_path, str) and skill_path else ref.name
    if not isinstance(leaf, str) or not leaf:
        return None
    return f"skill/github/{source}/{leaf.strip('/')}"


def _normalize_github_source(source: str) -> str:
    value = source.strip().removesuffix(".git")
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "git+https://github.com/",
        "github.com/",
    ):
        if value.lower().startswith(prefix):
            return value[len(prefix) :].strip("/")
    return value.strip("/")


def _plugin_private_identity(
    ref: Any,
    role: str,
    parent_identity: str | None,
) -> str | None:
    if not parent_identity or not parent_identity.startswith("plugin/"):
        return None
    name = ref.name or _component_path_leaf(ref, str((ref.extra or {}).get("component_type")))
    if not isinstance(name, str) or not name:
        return None
    return f"{role}/{parent_identity}/{name}"


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
