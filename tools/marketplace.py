"""Canonical Claude marketplace settings and comparison keys."""

from __future__ import annotations

from urllib.parse import urlparse


def key(value: str) -> tuple[str, str]:
    """Return the host-native comparison key for a marketplace source."""
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.hostname == "github.com":
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
