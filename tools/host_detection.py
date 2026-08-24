"""Detection and validation of endpoint agent-host configuration layouts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class EndpointHost:
    """A supported endpoint host and its resolved configuration directory."""

    host_id: str
    host_surface: str
    config_dir: Path


class HostConfigNotFound(ValueError):
    """Raised when endpoint scanning cannot select a usable host layout."""


_CLAUDE_HOST_ID = "claude-code"
_CLAUDE_HOST_SURFACE = "Claude Code"


def resolve_endpoint_host(
    config_dir: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> EndpointHost:
    """Select and validate a supported endpoint host configuration.

    ``--config-dir`` and ``CLAUDE_CONFIG_DIR`` designate Claude Code's config
    layout, as documented by the endpoint commands. An explicitly configured
    path is authoritative: a bad value is reported instead of silently falling
    back to another directory. Without either override, supported default
    layouts are probed and selected only when their directory exists.
    """
    environment = os.environ if environ is None else environ

    if config_dir is not None:
        return _claude_host(config_dir.expanduser(), source="--config-dir")

    configured = environment.get("CLAUDE_CONFIG_DIR")
    if configured:
        return _claude_host(Path(configured).expanduser(), source="CLAUDE_CONFIG_DIR")

    claude_dir = (Path.home() if home is None else home) / ".claude"
    if claude_dir.is_dir():
        return EndpointHost(_CLAUDE_HOST_ID, _CLAUDE_HOST_SURFACE, claude_dir)

    raise HostConfigNotFound(
        "no supported agent host config found; "
        f"checked Claude Code config at {claude_dir}. "
        "Set CLAUDE_CONFIG_DIR or pass --config-dir to an existing directory."
    )


def _claude_host(path: Path, *, source: str) -> EndpointHost:
    if not path.is_dir():
        raise HostConfigNotFound(
            f"Claude Code config directory from {source} does not exist "
            f"or is not a directory: {path}"
        )
    return EndpointHost(_CLAUDE_HOST_ID, _CLAUDE_HOST_SURFACE, path)
