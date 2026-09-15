"""Normalize Pi package sources for deduplication and posture checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

_EXACT_SEMVER_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_NPM_NAME_RE = re.compile(r"^(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+$")
_GIT_PROTOCOL_RE = re.compile(r"^(?:https?|ssh|git)://", re.IGNORECASE)
_SCP_GIT_RE = re.compile(r"^git@(?P<host>[^/:]+):(?P<path>.+)$")


@dataclass(frozen=True)
class PiSource:
    kind: Literal["npm", "git", "local"]
    identity: str
    ref: str | None
    purl: str | None
    pinned: bool


def parse_pi_source(raw: str, *, base_dir: Path | None = None) -> PiSource | None:
    """Parse a configured source using Pi 0.85.1's canonical dedup identities.

    ``base_dir`` is the directory containing the settings file. It matters for
    local declarations because Pi resolves their identity relative to that file.
    """
    source = (raw or "").strip()
    if not source:
        return None

    if source.startswith("npm:"):
        return _parse_npm_source(source)

    npm = _parse_npm_source(source)
    if npm is not None:
        return npm

    if source.startswith("git:") or _GIT_PROTOCOL_RE.match(source):
        return _parse_git_source(source)

    return _parse_local_source(source, base_dir)


def _parse_npm_source(source: str) -> PiSource | None:
    explicit = source.startswith("npm:")
    spec = source[len("npm:") :].strip() if explicit else source
    name, ref = _split_npm_spec(spec)
    if not _valid_npm_name(name):
        return None

    pinned = ref is not None and _EXACT_SEMVER_RE.fullmatch(ref) is not None
    purl = _npm_purl(name, ref) if pinned and ref is not None else None
    return PiSource("npm", f"npm:{name}", ref, purl, pinned)


def _valid_npm_name(name: str) -> bool:
    if _NPM_NAME_RE.fullmatch(name) is None:
        return False
    return all(part not in {".", ".."} for part in name.removeprefix("@").split("/"))


def _split_npm_spec(spec: str) -> tuple[str, str | None]:
    last_at = spec.rfind("@")
    if last_at <= 0:
        return spec, None
    return spec[:last_at], spec[last_at + 1 :] or None


def _npm_purl(name: str, version: str) -> str:
    if name.startswith("@"):
        scope, separator, package = name[1:].partition("/")
        if separator:
            return f"pkg:npm/%40{scope}/{package}@{version}"
    return f"pkg:npm/{name}@{version}"


def _parse_git_source(source: str) -> PiSource | None:
    explicit = source.startswith("git:") and not source.startswith("git://")
    value = source[len("git:") :].strip() if explicit else source
    if not explicit and _GIT_PROTOCOL_RE.match(value) is None:
        return None

    if _GIT_PROTOCOL_RE.match(value):
        parsed = urlsplit(value)
        if not parsed.hostname:
            return None
        path, ref = _split_git_path(unquote(parsed.path).lstrip("/"))
        ref = ref or parsed.fragment or None
        return _git_source(parsed.hostname, path, ref)

    scp_match = _SCP_GIT_RE.fullmatch(value)
    if scp_match:
        path, ref = _split_git_path(unquote(scp_match.group("path")))
        return _git_source(scp_match.group("host"), path, ref)

    host, separator, path_with_ref = value.partition("/")
    if not separator or ("." not in host and host != "localhost"):
        return None
    path, ref = _split_git_path(unquote(path_with_ref))
    return _git_source(host, path, ref)


def _split_git_path(path: str) -> tuple[str, str | None]:
    path_without_fragment, fragment_separator, fragment = path.partition("#")
    repo, ref_separator, ref = path_without_fragment.rpartition("@")
    if ref_separator and repo and ref:
        return repo, ref
    if fragment_separator and path_without_fragment and fragment:
        return path_without_fragment, fragment
    return path, None


def _git_source(host: str, path: str, ref: str | None) -> PiSource | None:
    normalized_path = path.removesuffix(".git").strip("/")
    if (
        not host
        or not normalized_path
        or "/" not in normalized_path
        or "\\" in host
        or "\\" in normalized_path
        or "\0" in host
        or "\0" in normalized_path
        or any(part in {".", ".."} for part in normalized_path.split("/"))
    ):
        return None
    identity = f"git:{host.lower()}/{normalized_path}"
    return PiSource("git", identity, ref, None, bool(ref and _FULL_SHA_RE.fullmatch(ref)))


def _parse_local_source(source: str, base_dir: Path | None) -> PiSource:
    if source.startswith("file://"):
        path = Path(unquote(urlsplit(source).path))
    else:
        path = Path(source).expanduser()
    if not path.is_absolute():
        path = (base_dir or Path.cwd()) / path
    return PiSource("local", f"local:{path.resolve()}", None, None, False)
