"""Decide whether a string install reference is mutable (rolls forward) or
immutable (pins to a specific point in time).

Mutable: any ref a future pull can resolve differently — no version, @latest,
branch ref, tagged Docker image (tags can be re-pointed), bare image name.

Immutable: exact-version specifier (==X.Y.Z, @X.Y.Z where X.Y.Z is exact
semver), full git commit SHA, Docker image digest (@sha256:...).

Local filesystem paths and plain http/https URLs are NOT what this rule
cares about — they're either out of scope (locally checked-in) or handled
by other rules (insecure-transport). Return False for both so the
mutable-install rule doesn't double-flag them.
"""

from __future__ import annotations

import re

_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_DOCKER_DIGEST_RE = re.compile(r"@sha256:[a-f0-9]{64}$")
_EXACT_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?(\+[\w.]+)?$")


def is_mutable_reference(ref: str) -> bool:
    ref = ref.strip()

    # Local paths — out of scope for this rule.
    if ref.startswith(("./", "/", "file://", "~")):
        return False
    # Plain http(s) URLs (not git+http) — handled by insecure-transport rule.
    if ref.startswith(("http://", "https://")):
        return False

    # npx / uvx package specs
    for prefix in ("npx ", "uvx "):
        if ref.startswith(prefix):
            return _is_mutable_pkg_spec(ref[len(prefix) :].strip())

    # `uv tool run <spec>` — equivalent to uvx; skip any leading uv global
    # options (those start with `-`) to reach `tool run`.
    if ref.startswith("uv "):
        tokens = ref.split()
        i = 1
        while i < len(tokens) and tokens[i].startswith("-"):
            i += 1
        if i + 1 < len(tokens) and tokens[i] == "tool" and tokens[i + 1] == "run":
            for j in range(i + 2, len(tokens)):
                if not tokens[j].startswith("-"):
                    return _is_mutable_pkg_spec(tokens[j])
        return True  # unrecognized uv subcommand or no package argument

    # Git refs
    if ref.startswith(("git+", "git://")):
        return _is_mutable_git_ref(ref)

    # Docker image refs (heuristic: registry path with a `/`, no scheme).
    if "/" in ref:
        return _is_mutable_docker_ref(ref)

    # Opaque single-token reference — treat as mutable (conservative).
    return True


def _is_mutable_pkg_spec(spec: str) -> bool:
    """Inspect a `<name>[@version]` or `<name>==<version>` spec."""
    if "==" in spec:
        version = spec.split("==", 1)[1].strip()
        return not _EXACT_SEMVER_RE.match(version)
    last_at = spec.rfind("@")
    # rfind returns -1 when no '@' is present, or 0 when only the scoped-package
    # leader '@' is present. Both mean "no version pinned".
    if last_at <= 0:
        return True
    version = spec[last_at + 1 :].strip()
    if not version or version == "latest":
        return True
    return not _EXACT_SEMVER_RE.match(version)


def _is_mutable_git_ref(ref: str) -> bool:
    # git+https://host/x/y.git@<ref> or #<ref>
    for sep in ("@", "#"):
        # Use the LAST occurrence of the separator so we don't grab the @ in
        # git+https://user@host/... patterns.
        idx = ref.rfind(sep)
        if idx == -1:
            continue
        tail = ref[idx + 1 :].strip()
        # Strip any trailing query/path after the ref.
        tail = tail.split("?", 1)[0].split("/", 1)[0]
        if _SHA_RE.match(tail):
            return False
        return True
    return True  # no ref pinned at all


def _is_mutable_docker_ref(ref: str) -> bool:
    if _DOCKER_DIGEST_RE.search(ref):
        return False
    return True  # bare name, :latest, or any tag — all mutable
