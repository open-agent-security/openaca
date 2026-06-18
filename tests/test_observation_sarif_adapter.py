from pathlib import Path

from tools.observations import SarifObservationAdapter
from tools.parsers.claude_skill import parse


def _skill_ref(tmp_path: Path):
    skill_dir = tmp_path / "deploy-helper"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: deploy-helper\n"
        "description: Helps deploy services\n"
        "---\n"
        "Run the deploy checklist.\n"
    )
    return parse(skill_md)[0]


def test_sarif_adapter_preserves_source_attribution_and_subject_coordinate(
    tmp_path: Path,
) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "SkillSpector",
                        "semanticVersion": "1.2.3",
                        "rules": [
                            {
                                "id": "P1",
                                "shortDescription": {"text": "Instruction override"},
                                "properties": {"tags": ["prompt-injection"]},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "P1",
                        "level": "error",
                        "message": {"text": "Skill asks the agent to ignore prior instructions."},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "SKILL.md"},
                                    "region": {"startLine": 7},
                                }
                            }
                        ],
                        "properties": {"confidence": "high"},
                    }
                ],
            }
        ],
    }

    observations = SarifObservationAdapter(
        category_map={"P1": ["owasp-ast01", "prompt-injection"]}
    ).collect(ref, sarif)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.source == "SkillSpector"
    assert observation.source_version == "1.2.3"
    assert observation.observation_id == "P1"
    assert observation.title == "Instruction override"
    assert observation.severity == "high"
    assert observation.confidence == "high"
    assert observation.subject_coordinate.startswith("sha256:")
    assert observation.component == {
        "identity": "skill/deploy-helper",
        "name": "deploy-helper",
        "type": "skill",
    }
    assert observation.categories == ["owasp-ast01", "prompt-injection"]
    assert observation.evidence == {
        "sarif_rule_id": "P1",
        "sarif_level": "error",
        "sarif_message": "Skill asks the agent to ignore prior instructions.",
        "location_uri": "SKILL.md",
        "start_line": 7,
        "sarif_tags": ["prompt-injection"],
    }


def test_sarif_adapter_uses_security_severity_and_default_confidence(
    tmp_path: Path,
) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "bawbel",
                        "rules": [
                            {
                                "id": "AVE-2026-00001",
                                "shortDescription": {"text": "Credential access"},
                                "properties": {"security-severity": "9.4"},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "AVE-2026-00001",
                        "level": "warning",
                        "message": {"text": "Reads credential files."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter(source_version="unknown").collect(ref, sarif)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.source == "bawbel"
    assert observation.source_version == "unknown"
    assert observation.severity == "critical"
    assert observation.confidence == "medium"


def test_sarif_adapter_accepts_nested_rule_id(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {"driver": {"name": "scanner"}},
                "results": [
                    {
                        "rule": {"id": "nested-rule"},
                        "message": {"text": "Nested rule id shape."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    assert observations[0].observation_id == "nested-rule"


def test_sarif_adapter_resolves_rule_index(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [{"id": "R0", "shortDescription": {"text": "First rule"}}],
                    }
                },
                "results": [
                    {
                        "ruleIndex": 0,
                        "level": "warning",
                        "message": {"text": "Hit via ruleIndex."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    assert observations[0].observation_id == "R0"
    assert observations[0].title == "First rule"


def test_sarif_adapter_resolves_rule_dot_index(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [{"id": "R1", "shortDescription": {"text": "Second rule"}}],
                    }
                },
                "results": [
                    {
                        "rule": {"index": 0},
                        "level": "note",
                        "message": {"text": "Hit via rule.index."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    assert observations[0].observation_id == "R1"


def test_sarif_adapter_missing_level_defaults_to_warning(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [{"id": "NO-LEVEL"}],
                    }
                },
                "results": [{"ruleId": "NO-LEVEL", "message": {"text": "No level field."}}],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    # SARIF 2.1.0: absent level defaults to "warning" → medium severity
    assert observations[0].severity == "medium"


def test_sarif_adapter_uses_rule_default_configuration_level(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [
                            {
                                "id": "RULE-WITH-DEFAULT",
                                "defaultConfiguration": {"level": "error"},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "RULE-WITH-DEFAULT",
                        "message": {"text": "Level absent; rule configures error."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    assert observations[0].severity == "high"


def test_sarif_adapter_raw_tags_stay_in_evidence_not_categories(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [
                            {
                                "id": "TAGGED-RULE",
                                "shortDescription": {"text": "Tagged finding"},
                                "properties": {"tags": ["security", "external/cwe/CWE-77"]},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "TAGGED-RULE",
                        "message": {"text": "Finding with raw tags, no explicit mapping."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    observation = observations[0]
    # ADR-0034: without an explicit category_map entry, raw SARIF tags must NOT become categories
    assert observation.categories == []
    # Raw tags are preserved in evidence so the scanner signal is not lost
    assert observation.evidence["sarif_tags"] == ["security", "external/cwe/CWE-77"]
