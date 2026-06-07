"""Tests for `_align_posture_identities_to_bom`.

The posture rules emit `component_identity` as the leaf name (e.g.
`github`, `code-review`) but the Fleet backend joins findings to
`BomComponent` rows by the full `openaca:identity` (e.g.
`claude-plugin/claude-plugins-official/github`). Without alignment the
ingest rejects with "posture finding component_identity did not match
BOM component".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from tools.fleet.collector import _align_posture_identities_to_bom


def _bom_component(*, name: str, component_type: str, identity: str) -> dict[str, Any]:
    return {
        "type": "application",
        "name": name,
        "properties": [
            {"name": "openaca:component_type", "value": component_type},
            {"name": "openaca:identity", "value": identity},
        ],
    }


def _finding(*, component_type: str) -> MagicMock:
    finding = MagicMock()
    finding.component = {"type": component_type, "name": "ignored-by-alignment"}
    return finding


def test_aligns_plugin_leaf_name_to_full_identity() -> None:
    """The bug from production: posture finding says `github`, BOM says
    `claude-plugin/claude-plugins-official/github`. After alignment they
    agree.
    """
    bom = {
        "components": [
            _bom_component(
                name="github",
                component_type="plugin",
                identity="claude-plugin/claude-plugins-official/github",
            )
        ]
    }
    payloads: list[dict[str, Any]] = [
        {"rule_id": "openaca-posture-mutable-install-reference", "component_identity": "github"}
    ]
    findings = [_finding(component_type="plugin")]

    _align_posture_identities_to_bom(payloads, findings, bom)

    assert payloads[0]["component_identity"] == "claude-plugin/claude-plugins-official/github"


def test_disambiguates_by_component_type_when_name_collides() -> None:
    """Some leaf names appear under multiple types in the BOM (a name
    collision: `code-review` is both a plugin AND a command bundled by
    that plugin). The alignment uses the finding's `component["type"]`
    to pick the right BOM identity.
    """
    bom = {
        "components": [
            _bom_component(
                name="code-review",
                component_type="plugin",
                identity="claude-plugin/claude-plugins-official/code-review",
            ),
            _bom_component(
                name="code-review",
                component_type="command",
                identity="claude-command/code-review/code-review",
            ),
        ]
    }
    payloads: list[dict[str, Any]] = [
        {"component_identity": "code-review"},
        {"component_identity": "code-review"},
    ]
    findings = [
        _finding(component_type="plugin"),
        _finding(component_type="command"),
    ]

    _align_posture_identities_to_bom(payloads, findings, bom)

    assert payloads[0]["component_identity"] == "claude-plugin/claude-plugins-official/code-review"
    assert payloads[1]["component_identity"] == "claude-command/code-review/code-review"


def test_leaves_payload_alone_when_no_bom_match() -> None:
    """If the BOM has no matching component (e.g. because the BOM
    component lacks `openaca:identity` altogether — a separate, deeper
    bug), the alignment is a no-op. The backend's existing rejection
    error then surfaces the actual problem instead of silently
    succeeding with a wrong value.
    """
    bom = {
        "components": [
            {
                "type": "application",
                "name": "@playwright/mcp",
                "properties": [
                    {"name": "openaca:component_type", "value": "mcp_server"},
                    # NOTE: no openaca:identity property
                ],
            }
        ]
    }
    payloads: list[dict[str, Any]] = [{"component_identity": "@playwright/mcp"}]
    findings = [_finding(component_type="mcp_server")]

    _align_posture_identities_to_bom(payloads, findings, bom)

    assert payloads[0]["component_identity"] == "@playwright/mcp"


def test_skips_findings_without_string_component_identity() -> None:
    """Asset-scope findings (`openaca-posture-api-endpoint-override`)
    carry `component_identity=None` because they're not bound to a
    component. The alignment must not crash on them.
    """
    bom = {"components": []}
    payloads: list[dict[str, Any]] = [
        {"rule_id": "openaca-posture-api-endpoint-override", "component_identity": None}
    ]
    findings = [_finding(component_type="asset")]

    _align_posture_identities_to_bom(payloads, findings, bom)

    assert payloads[0]["component_identity"] is None


def test_uses_first_bom_match_when_duplicate_keys_present() -> None:
    """When the BOM has two components with the same `(type, name)` —
    e.g., duplicate hook declarations from multiple plugin manifests
    pointing at the same hash identity — `setdefault` keeps the first
    one. This is intentional: identical hashed hooks are by definition
    the same component, so any of them is the right answer.
    """
    shared_identity = "claude-hook/hook:a3fd7e17b2bab038"
    bom = {
        "components": [
            _bom_component(
                name="claude-hook/hook:a3fd7e17b2bab038",
                component_type="hook",
                identity=shared_identity,
            ),
            _bom_component(
                name="claude-hook/hook:a3fd7e17b2bab038",
                component_type="hook",
                identity=shared_identity,
            ),
        ]
    }
    payloads: list[dict[str, Any]] = [{"component_identity": "claude-hook/hook:a3fd7e17b2bab038"}]
    findings = [_finding(component_type="hook")]

    _align_posture_identities_to_bom(payloads, findings, bom)

    assert payloads[0]["component_identity"] == shared_identity
