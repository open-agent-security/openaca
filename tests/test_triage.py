from tools.triage import build_triage_cards


def test_vulnerable_package_groups_under_agent_component_with_upgrade_action():
    scan_doc = {
        "findings": [
            {
                "finding_type": "vulnerability",
                "id": "GHSA-test",
                "title": "Package vulnerability",
                "severity": "HIGH",
                "confidence": "high",
                "fixed_in": "2.0.0",
                "source": "osv.dev",
                "component": {"type": "package", "name": "lodash"},
                "component_path": [
                    {"type": "plugin", "name": "demo"},
                    {"type": "mcp_server", "name": "files"},
                    {"type": "package", "name": "lodash"},
                ],
                "attributed_to": "plugin/demo@1.0.0",
            }
        ]
    }

    cards = build_triage_cards(scan_doc)

    assert len(cards) == 1
    assert cards[0].component_id == "plugin/demo@1.0.0"
    assert cards[0].component_type == "plugin"
    assert cards[0].priority == "high"
    assert cards[0].action == "upgrade"
    assert cards[0].evidence[0].id == "GHSA-test"


def test_multiple_findings_group_under_same_component_and_rank_deterministically():
    scan_doc = {
        "findings": [
            {
                "finding_type": "vulnerability",
                "id": "GHSA-b",
                "severity": "MEDIUM",
                "confidence": "high",
                "component": {"type": "package", "name": "b"},
                "component_path": [{"type": "plugin", "name": "beta"}],
            },
            {
                "finding_type": "posture",
                "rule_id": "openaca-posture-mutable-install",
                "title": "Mutable install source",
                "severity": "high",
                "confidence": "medium",
                "component": {"type": "plugin", "name": "alpha"},
                "component_path": [{"type": "plugin", "name": "alpha"}],
                "remediation": "Pin mutable install source.",
            },
            {
                "finding_type": "vulnerability",
                "id": "GHSA-a",
                "severity": "HIGH",
                "confidence": "high",
                "component": {"type": "package", "name": "a"},
                "component_path": [{"type": "plugin", "name": "alpha"}],
            },
        ]
    }

    cards = build_triage_cards(scan_doc)

    assert [card.component_label for card in cards] == ["alpha", "beta"]
    assert cards[0].action == "pin"
    assert [item.id for item in cards[0].evidence] == [
        "openaca-posture-mutable-install",
        "GHSA-a",
    ]


def test_posture_finding_groups_by_component_identity_not_display_label():
    scan_doc = {
        "findings": [
            {
                "finding_type": "posture",
                "rule_id": "openaca-posture-mutable-install-reference",
                "title": "Mutable install source",
                "severity": "low",
                "confidence": "high",
                "component": {
                    "type": "plugin",
                    "name": "shared",
                    "identity": "plugin/marketplace-a/shared",
                },
                "component_path": [{"type": "plugin", "name": "shared"}],
                "remediation": "Pin mutable install source.",
            },
            {
                "finding_type": "posture",
                "rule_id": "openaca-posture-mutable-install-reference",
                "title": "Mutable install source",
                "severity": "low",
                "confidence": "high",
                "component": {
                    "type": "plugin",
                    "name": "shared",
                    "identity": "plugin/marketplace-b/shared",
                },
                "component_path": [{"type": "plugin", "name": "shared"}],
                "remediation": "Pin mutable install source.",
            },
        ]
    }

    cards = build_triage_cards(scan_doc)

    assert len(cards) == 2
    assert {card.component_id for card in cards} == {
        "plugin/marketplace-a/shared",
        "plugin/marketplace-b/shared",
    }


def test_low_confidence_observation_defaults_to_review():
    scan_doc = {
        "findings": [
            {
                "finding_type": "observation",
                "observation_id": "X007",
                "title": "Could not reach scanner",
                "severity": "info",
                "confidence": "low",
                "source": "skillspector",
                "component": {"type": "skill", "name": "frontend"},
                "component_path": [{"type": "skill", "name": "frontend"}],
            }
        ]
    }

    cards = build_triage_cards(scan_doc)

    assert cards[0].action == "review"
    assert cards[0].confidence == "low"
    assert cards[0].evidence[0].provenance == "external-scanner-derived"
