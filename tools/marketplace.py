"""Canonical Claude marketplace settings and comparison keys."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_SCP_GIT_URL = re.compile(r"^[^@\s]+@[^:\s]+:[^\s/]+/.+$")


def key(value: str) -> tuple[str, str]:
    """Return the host-native comparison key for a marketplace source."""
    parsed = urlparse(value)
    if value != value.strip() or not value:
        raise ValueError("marketplace source must be a valid source URL")
    if parsed.query or parsed.fragment:
        raise ValueError("marketplace source must not contain a query or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("marketplace source must be a valid source URL") from exc
    if not (
        (
            parsed.scheme in {"git", "http", "https", "ssh", "git+http", "git+https", "git+ssh"}
            and parsed.hostname
            and parsed.path
        )
        or _SCP_GIT_URL.match(value)
    ):
        raise ValueError("marketplace source must be a valid source URL")
    if parsed.scheme in {"http", "https"} and parsed.hostname == "github.com":
        if parsed.netloc != "github.com":
            raise ValueError("marketplace source must be a valid source URL")
        repo = parsed.path.strip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if repo.count("/") == 1:
            return ("github", repo)
    return ("git", value)


def setting(value: str) -> dict[str, str]:
    """Return Claude's managed-settings representation for a marketplace source."""
    source, location = key(value)
    return {"source": source, "repo" if source == "github" else "url": location}
