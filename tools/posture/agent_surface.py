"""Resolve an installed agent kind's own posture surface.

A submodule rather than part of `tools/posture/__init__.py`: these functions
call `kind_for`, and `tools/agent_kinds`' kind modules import `tools.posture`,
so a `tools.agent_kinds` import at the top of the posture package's `__init__`
risks a cycle. `no_manifests` stays in `tools/posture/__init__.py` and stays
shared; this module imports it from there.
"""

from __future__ import annotations

from pathlib import Path

from tools.agent_kinds import AgentInstance, kind_for
from tools.component_ref import ComponentRef
from tools.posture import no_manifests

__all__ = ["agent_extra_posture_manifests", "agent_posture_manifests"]


def agent_extra_posture_manifests(
    agent: AgentInstance, refs: list[ComponentRef]
) -> dict[str, list[tuple[Path, dict]]]:
    """Rule-id-keyed manifests for posture surfaces that declare no components.

    Empty for every kind that declares none, which is every kind but Codex.
    """
    collectors = kind_for(agent.kind_id).extra_installed_posture_collectors or {}
    return {
        rule_id: collector(agent.config_root, agent.project_root, refs)
        for rule_id, collector in collectors.items()
    }


def agent_posture_manifests(
    agent: AgentInstance, refs: list[ComponentRef]
) -> tuple[list[tuple[Path, dict]], list[tuple[Path, dict]]]:
    """Read the kind's own installed posture surface, not a Claude-Code-shaped
    collector called unconditionally for every kind.

    Isolated as a helper for the same reason as `_agent_refs`: tests
    monkeypatch this single boundary.
    """
    mcp_collector, settings_collector = kind_for(agent.kind_id).installed_posture_collectors or (
        no_manifests,
        no_manifests,
    )
    return (
        mcp_collector(agent.config_root, agent.project_root, refs),
        # For Cursor, `settings_collector` is `collect_cursor_endpoint_permissions_manifests`,
        # which accepts `agent.config_root` but resolves via
        # CURSOR_CONFIG_DIR/XDG_CONFIG_HOME/home instead — `permissions.json` is
        # the one Cursor surface those variables relocate, so `--config-dir`
        # does not relocate it here either. Not a bug to fix at this call site.
        settings_collector(agent.config_root, agent.project_root),
    )
