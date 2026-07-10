from tools.triage import build_exposure_cards, decide_exposure


def _path(
    *,
    plugin_bom_ref: str = "plugin-occurrence",
    plugin_identity: str | None = "plugin/marketplace/demo",
    plugin_version: str = "1.0.0",
    package_bom_ref: str = "package-occurrence",
) -> list[dict]:
    return [
        {
            "type": "plugin",
            "name": "demo",
            "bom_ref": plugin_bom_ref,
            "identity": plugin_identity,
            "version": plugin_version,
        },
        {
            "type": "package",
            "name": "lodash",
            "bom_ref": package_bom_ref,
            "identity": "package/npm/lodash",
            "version": "4.17.20",
        },
    ]


def _vulnerability(**overrides: object) -> dict:
    finding = {
        "finding_type": "vulnerability",
        "id": "GHSA-test",
        "title": "Package vulnerability",
        "severity": "HIGH",
        "confidence": "high",
        "fixed_in": "4.17.21",
        "source": "osv.dev",
        "bom_ref": "package-occurrence",
        "component": {
            "type": "package",
            "name": "lodash",
            "identity": "package/npm/lodash",
            "source": {"purl": "pkg:npm/lodash@4.17.20"},
        },
        "component_path": _path(),
    }
    finding.update(overrides)
    return finding


def test_exposure_card_separates_component_identity_from_occurrences() -> None:
    cards = build_exposure_cards({"findings": [_vulnerability()]})

    assert len(cards) == 1
    card = cards[0]
    assert card.component.to_dict() == {
        "identity": "plugin/marketplace/demo",
        "type": "plugin",
        "name": "demo",
        "versions": ["1.0.0"],
    }
    assert [occurrence.to_dict() for occurrence in card.occurrences] == [
        {
            "bom_ref": "plugin-occurrence",
            "composition_paths": [_path()],
            "active_in": [],
        }
    ]
    assert card.priority == "high"
    assert card.action == "upgrade"
    assert card.evidence[0].bom_ref == "package-occurrence"


def test_same_identity_rolls_versions_and_occurrences_into_one_card() -> None:
    second = _vulnerability(
        id="GHSA-second",
        bom_ref="package-occurrence-2",
        component_path=_path(
            plugin_bom_ref="plugin-occurrence-2",
            plugin_version="1.1.0",
            package_bom_ref="package-occurrence-2",
        ),
    )

    cards = build_exposure_cards({"findings": [_vulnerability(), second]})

    assert len(cards) == 1
    assert cards[0].component.versions == ["1.0.0", "1.1.0"]
    assert [item.bom_ref for item in cards[0].occurrences] == [
        "plugin-occurrence",
        "plugin-occurrence-2",
    ]
    assert {item.id for item in cards[0].evidence} == {"GHSA-test", "GHSA-second"}


def test_unidentified_aliases_remain_distinct_occurrences() -> None:
    first = _vulnerability(component_path=_path(plugin_identity=None))
    second = _vulnerability(
        id="GHSA-second",
        bom_ref="package-occurrence-2",
        component_path=_path(
            plugin_bom_ref="plugin-occurrence-2",
            plugin_identity=None,
            package_bom_ref="package-occurrence-2",
        ),
    )

    cards = build_exposure_cards({"findings": [first, second]})

    assert len(cards) == 2
    assert all(card.component.identity is None for card in cards)
    assert {card.occurrences[0].bom_ref for card in cards} == {
        "plugin-occurrence",
        "plugin-occurrence-2",
    }


def test_direct_package_backed_mcps_group_by_role_qualified_identity() -> None:
    def finding(alias: str, bom_ref: str, identity: str, version: str) -> dict:
        return {
            "finding_type": "posture",
            "rule_id": f"posture-{bom_ref}",
            "title": "Mutable install source",
            "severity": "low",
            "confidence": "high",
            "bom_ref": bom_ref,
            "component": {"type": "mcp_server", "name": alias, "identity": identity},
            "component_path": [
                {
                    "type": "mcp_server",
                    "name": alias,
                    "bom_ref": bom_ref,
                    "identity": identity,
                    "version": version,
                }
            ],
            "remediation": "Pin the package version.",
        }

    shared = "mcp-server/npm/@modelcontextprotocol/server-filesystem"
    scan_doc = {
        "findings": [
            finding("fs", "mcp-a", shared, "1.0.0"),
            finding("files", "mcp-b", shared, "1.1.0"),
            finding("files", "mcp-c", "mcp-server/npm/other", "2.0.0"),
        ]
    }

    cards = build_exposure_cards(scan_doc)

    assert len(cards) == 2
    shared_card = next(card for card in cards if card.component.identity == shared)
    assert shared_card.component.versions == ["1.0.0", "1.1.0"]
    assert {item.bom_ref for item in shared_card.occurrences} == {"mcp-a", "mcp-b"}


def test_multiple_finding_types_share_decision_rules() -> None:
    posture = {
        "finding_type": "posture",
        "rule_id": "openaca-posture-mutable-install-reference",
        "title": "Mutable install source",
        "severity": "high",
        "confidence": "medium",
        "bom_ref": "plugin-occurrence",
        "component": {
            "type": "plugin",
            "name": "demo",
            "identity": "plugin/marketplace/demo",
        },
        "component_path": _path()[:1],
        "remediation": "Pin mutable install source.",
    }

    cards = build_exposure_cards({"findings": [_vulnerability(), posture]})

    assert len(cards) == 1
    assert cards[0].action == "pin"
    assert [item.id for item in cards[0].evidence] == [
        "openaca-posture-mutable-install-reference",
        "GHSA-test",
    ]


def test_malware_action_takes_precedence() -> None:
    malware = _vulnerability(id="MAL-2026-1234", severity="critical", fixed_in=None)

    card = build_exposure_cards({"findings": [malware]})[0]

    assert card.action == "remove"


def test_fixed_vulnerability_recommends_upgrade_despite_http_in_title() -> None:
    """A `fixed_in` vulnerability whose id/title happens to mention "http" (e.g.
    an HTTP request-smuggling advisory) must still recommend the known upgrade,
    not the generic transport "replace" heuristic."""
    finding = _vulnerability(id="GHSA-http-smuggling", title="HTTP request smuggling in lodash")

    card = build_exposure_cards({"findings": [finding]})[0]

    assert card.action == "upgrade"


def test_low_confidence_observation_defaults_to_review() -> None:
    finding = {
        "finding_type": "observation",
        "observation_id": "X007",
        "title": "Could not reach scanner",
        "severity": "info",
        "confidence": "low",
        "source": "skillspector",
        "bom_ref": "skill-occurrence",
        "component": {"type": "skill", "name": "frontend", "identity": None},
        "component_path": [
            {
                "type": "skill",
                "name": "frontend",
                "bom_ref": "skill-occurrence",
                "identity": None,
            }
        ],
    }

    card = build_exposure_cards({"findings": [finding]})[0]

    assert card.action == "review"
    assert card.confidence == "low"
    assert card.evidence[0].provenance == "external-scanner-derived"


def test_component_scoped_finding_requires_an_occurrence_key() -> None:
    finding = _vulnerability(bom_ref=None, component_path=[])

    try:
        build_exposure_cards({"findings": [finding]})
    except ValueError as exc:
        assert "bom_ref" in str(exc)
    else:
        raise AssertionError("expected a missing bom_ref error")


def test_component_path_drops_unresolvable_ancestor_from_graphless_scan() -> None:
    """A flat/pre-Stage-4 BOM re-scan (`scan bom` with no reconstructable graph)
    only ever carries a bom_ref for the leaf occurrence; a stored ancestor node
    (e.g. a bundling plugin) has none. That must not crash triage — the
    unresolvable ancestor is dropped, leaving the occurrence that does have an
    exact bom_ref."""
    finding = _vulnerability(
        component_path=[
            {"type": "plugin", "name": "acme-devtools"},
            {
                "type": "package",
                "name": "lodash",
                "bom_ref": "package-occurrence",
                "identity": "package/npm/lodash",
                "version": "4.17.20",
            },
        ]
    )

    cards = build_exposure_cards({"findings": [finding]})

    assert len(cards) == 1
    path = cards[0].occurrences[0].composition_paths[0]
    assert [node.type for node in path] == ["package"]
    assert path[0].bom_ref == "package-occurrence"


def test_asset_scoped_posture_does_not_become_a_component_exposure() -> None:
    finding = {
        "finding_type": "posture",
        "rule_id": "openaca-posture-api-endpoint-override",
        "title": "Claude API endpoint is overridden",
        "severity": "high",
        "confidence": "medium",
        "component": {"type": "agent_config", "name": "claude-settings/api-endpoint"},
        "component_path": [{"type": "agent_config", "name": "api-endpoint"}],
    }

    assert build_exposure_cards({"findings": [finding]}) == []


def test_asset_scoped_mcp_server_posture_does_not_crash_exposure_report() -> None:
    # openaca-posture-insecure-transport and openaca-posture-mcp-auto-approve
    # describe a manifest-level MCP server entry, not a scanned BOM occurrence,
    # so they never carry a bom_ref even though their component type
    # ("mcp_server") is also used by real, bom_ref-bearing BOM components.
    insecure_transport = {
        "finding_type": "posture",
        "rule_id": "openaca-posture-insecure-transport",
        "title": "Remote MCP endpoint uses insecure transport",
        "severity": "medium",
        "confidence": "high",
        "component": {
            "type": "mcp_server",
            "name": "mcp-server/demo @ http://example.com",
        },
        "component_path": [{"type": "mcp_server", "name": "mcp-server/demo"}],
    }
    auto_approve = {
        "finding_type": "posture",
        "rule_id": "openaca-posture-mcp-auto-approve",
        "title": "MCP server has auto-approval enabled",
        "severity": "medium",
        "confidence": "medium",
        "component": {"type": "mcp_server", "name": "mcp-server/demo autoApprove"},
        "component_path": [{"type": "mcp_server", "name": "mcp-server/demo"}],
    }

    assert build_exposure_cards({"findings": [insecure_transport, auto_approve]}) == []


def test_shared_decision_function_recomputes_action_for_merged_evidence() -> None:
    vulnerability_card = build_exposure_cards({"findings": [_vulnerability()]})[0]
    posture = {
        "finding_type": "posture",
        "rule_id": "openaca-posture-mutable-install-reference",
        "title": "Mutable install source",
        "severity": "medium",
        "confidence": "high",
        "bom_ref": "plugin-occurrence",
        "component": {
            "type": "plugin",
            "name": "demo",
            "identity": "plugin/marketplace/demo",
        },
        "component_path": _path()[:1],
        "remediation": "Pin mutable install source.",
    }
    posture_card = build_exposure_cards({"findings": [posture]})[0]

    decision = decide_exposure(
        vulnerability_card.component,
        [*vulnerability_card.evidence, *posture_card.evidence],
        [
            path
            for card in (vulnerability_card, posture_card)
            for occurrence in card.occurrences
            for path in occurrence.composition_paths
        ],
    )

    assert vulnerability_card.action == "upgrade"
    assert decision.action == "pin"
