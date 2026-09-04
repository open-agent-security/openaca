"""Facade re-export: identity and match-coordinate helpers. See ADR-0028."""

from tools.identity import (
    MatchCoordinate,
    is_mcp_package_launch_install_source,
    match_coordinates,
    safe_pinned_mcp_install_source,
    safe_unpinned_mcp_install_source,
)

__all__ = [
    "MatchCoordinate",
    "is_mcp_package_launch_install_source",
    "match_coordinates",
    "safe_pinned_mcp_install_source",
    "safe_unpinned_mcp_install_source",
]
