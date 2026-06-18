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


def test_sarif_adapter_resolves_extension_rule_metadata(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {"name": "scanner"},
                    "extensions": [
                        {
                            "name": "ext-plugin",
                            "rules": [
                                {
                                    "id": "EXT-001",
                                    "shortDescription": {"text": "Extension rule title"},
                                    "defaultConfiguration": {"level": "error"},
                                    "help": {"text": "See docs for remediation."},
                                    "properties": {"tags": ["ext-tag"]},
                                }
                            ],
                        }
                    ],
                },
                "results": [
                    {
                        "ruleId": "EXT-001",
                        "message": {"text": "Triggered extension rule."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.observation_id == "EXT-001"
    # Extension rule metadata must be resolved (not degraded to rule_id fallbacks)
    assert observation.title == "Extension rule title"
    assert observation.severity == "high"  # defaultConfiguration.level "error" → high
    assert observation.remediation == "See docs for remediation."
    assert observation.evidence.get("sarif_tags") == ["ext-tag"]


def test_sarif_adapter_resolves_extension_rule_by_tool_component_index(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [{"id": "DRIVER-R0", "shortDescription": {"text": "Driver rule"}}],
                    },
                    "extensions": [
                        {
                            "name": "plugin-a",
                            "rules": [
                                {"id": "EXT-A-R0", "shortDescription": {"text": "Extension A rule"}}
                            ],
                        }
                    ],
                },
                "results": [
                    {
                        # Rule identified only by index into extension 0's rules (no ruleId string)
                        "rule": {"index": 0, "toolComponent": {"index": 0}},
                        "message": {"text": "Extension rule by toolComponent.index."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    assert observations[0].observation_id == "EXT-A-R0"
    assert observations[0].title == "Extension A rule"


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


def test_sarif_adapter_skips_pass_kind_results(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [
                            {"id": "CHECK-001", "shortDescription": {"text": "Check passed"}}
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "CHECK-001",
                        "kind": "pass",
                        "message": {"text": "This check passed."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    # SARIF kind "pass" is not a security finding — must be filtered out
    assert observations == []


def test_sarif_adapter_skips_not_applicable_kind_results(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [
                            {"id": "N/A-001", "shortDescription": {"text": "Not applicable"}}
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "N/A-001",
                        "kind": "notApplicable",
                        "message": {"text": "Rule did not apply."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    # SARIF kind "notApplicable" is not a security finding — must be filtered out
    assert observations == []


def test_sarif_adapter_skips_informational_kind_results(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [
                            {
                                "id": "INFO-001",
                                "shortDescription": {"text": "Tool version notice"},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "INFO-001",
                        "kind": "informational",
                        "message": {"text": "Scanner version 1.2.3 used."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    # SARIF kind "informational" is a notification for the user, not a problem — must be filtered
    assert observations == []


def test_sarif_adapter_only_emits_findings_from_mixed_kinds(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [
                            {"id": "RULE-FAIL", "shortDescription": {"text": "Failing rule"}},
                            {"id": "RULE-PASS", "shortDescription": {"text": "Passing rule"}},
                            {"id": "RULE-NA", "shortDescription": {"text": "N/A rule"}},
                        ],
                    }
                },
                "results": [
                    {"ruleId": "RULE-FAIL", "kind": "fail", "message": {"text": "Actual finding."}},
                    {"ruleId": "RULE-PASS", "kind": "pass", "message": {"text": "Check passed."}},
                    {
                        "ruleId": "RULE-NA",
                        "kind": "notApplicable",
                        "message": {"text": "Not applicable."},
                    },
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    # Only the "fail" result becomes an observation
    assert len(observations) == 1
    assert observations[0].observation_id == "RULE-FAIL"


def test_sarif_adapter_index_takes_priority_over_id_dict_for_duplicate_rule_ids(
    tmp_path: Path,
) -> None:
    ref = _skill_ref(tmp_path)
    # Two rules share the same id "DUP"; _rules_by_id collapses them (last wins).
    # ruleIndex=0 on the result must pin the *first* rule, not the dict survivor.
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [
                            {
                                "id": "DUP",
                                "shortDescription": {"text": "First DUP rule"},
                                "defaultConfiguration": {"level": "error"},
                            },
                            {
                                "id": "DUP",
                                "shortDescription": {"text": "Second DUP rule"},
                                "defaultConfiguration": {"level": "note"},
                            },
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "DUP",
                        "ruleIndex": 0,
                        "message": {"text": "Finding matched to first DUP rule by index."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.observation_id == "DUP"
    # Index 0 → "First DUP rule" with level "error" → severity "high"
    assert observation.title == "First DUP rule"
    assert observation.severity == "high"


def test_sarif_adapter_resolves_extension_rule_by_tool_component_name(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    # SARIF 2.1.0 toolComponentReference: name instead of numeric index
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [{"id": "DRIVER-R0", "shortDescription": {"text": "Driver rule"}}],
                    },
                    "extensions": [
                        {
                            "name": "my-plugin",
                            "rules": [
                                {
                                    "id": "EXT-NAME-R0",
                                    "shortDescription": {"text": "Extension rule by name"},
                                    "defaultConfiguration": {"level": "error"},
                                }
                            ],
                        }
                    ],
                },
                "results": [
                    {
                        # toolComponent identified by name, not numeric index
                        "rule": {"index": 0, "toolComponent": {"name": "my-plugin"}},
                        "message": {"text": "Extension rule resolved via toolComponent.name."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.observation_id == "EXT-NAME-R0"
    assert observation.title == "Extension rule by name"
    assert observation.severity == "high"  # defaultConfiguration.level "error" → high


def test_sarif_adapter_resolves_extension_rule_by_tool_component_guid(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    guid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {"name": "scanner"},
                    "extensions": [
                        {
                            "name": "ext-guid",
                            "guid": guid,
                            "rules": [
                                {
                                    "id": "EXT-GUID-R0",
                                    "shortDescription": {"text": "Extension rule by guid"},
                                }
                            ],
                        }
                    ],
                },
                "results": [
                    {
                        "rule": {"index": 0, "toolComponent": {"guid": guid}},
                        "message": {"text": "Extension rule resolved via toolComponent.guid."},
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    assert observations[0].observation_id == "EXT-GUID-R0"
    assert observations[0].title == "Extension rule by guid"


def test_sarif_adapter_resolves_artifact_location_by_index(tmp_path: Path) -> None:
    ref = _skill_ref(tmp_path)
    # SARIF 2.1.0: artifactLocation.index resolves into run.artifacts[index].location.uri
    sarif = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "scanner",
                        "rules": [{"id": "ART-001", "shortDescription": {"text": "Artifact rule"}}],
                    }
                },
                "artifacts": [
                    {"location": {"uri": "src/skill/SKILL.md"}},
                ],
                "results": [
                    {
                        "ruleId": "ART-001",
                        "message": {"text": "Finding in indexed artifact."},
                        "locations": [
                            {
                                "physicalLocation": {
                                    # URI absent; index points into run.artifacts
                                    "artifactLocation": {"index": 0},
                                    "region": {"startLine": 12},
                                }
                            }
                        ],
                    }
                ],
            }
        ]
    }

    observations = SarifObservationAdapter().collect(ref, sarif)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.evidence.get("location_uri") == "src/skill/SKILL.md"
    assert observation.evidence.get("start_line") == 12
    assert observation.declared_by == {"kind": "sarif", "path": "src/skill/SKILL.md"}


def test_sarif_adapter_prefers_hierarchical_result_rule_id_over_indexed_descriptor(
    tmp_path: Path,
) -> None:
    # SARIF 2.1.0 §3.52.4: result.ruleId may extend the indexed descriptor id with
    # extra hierarchical components. The observation identity (and category_map key)
    # must use the more specific result id, not the descriptor's base id.
    ref = _skill_ref(tmp_path)
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "SkillSpector",
                        "rules": [
                            {"id": "P1", "shortDescription": {"text": "Instruction override"}}
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "P1/ignore-instructions",
                        "ruleIndex": 0,
                        "level": "error",
                        "message": {"text": "Ignore prior instructions."},
                    }
                ],
            }
        ],
    }

    observations = SarifObservationAdapter(
        category_map={"P1/ignore-instructions": ["owasp-ast01"]}
    ).collect(ref, sarif)

    assert len(observations) == 1
    observation = observations[0]
    # Specific result id wins for identity...
    assert observation.observation_id == "P1/ignore-instructions"
    # ...while metadata still comes from the indexed descriptor.
    assert observation.title == "Instruction override"
    # ...and the category_map keyed on the emitted id applies.
    assert observation.categories == ["owasp-ast01"]


def test_sarif_adapter_honors_invocation_severity_override(tmp_path: Path) -> None:
    # SARIF 2.1.0 §3.20.5: when result.level is absent, an invocation
    # ruleConfigurationOverride re-levels the rule and takes precedence over the
    # rule's defaultConfiguration.
    ref = _skill_ref(tmp_path)

    def _sarif(with_invocation_pointer: bool) -> dict:
        result = {
            "ruleId": "P2",
            "ruleIndex": 0,
            "message": {"text": "Possible tool poisoning."},
        }
        if with_invocation_pointer:
            result["provenance"] = {"invocationIndex": 0}
        return {
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "SkillSpector",
                            "rules": [{"id": "P2", "defaultConfiguration": {"level": "note"}}],
                        }
                    },
                    "invocations": [
                        {
                            "ruleConfigurationOverrides": [
                                {
                                    "descriptor": {"id": "P2", "index": 0},
                                    "configuration": {"level": "error"},
                                }
                            ]
                        }
                    ],
                    "results": [result],
                }
            ],
        }

    # With the invocation override in effect, "error" -> high (not the default "note" -> low).
    overridden = SarifObservationAdapter().collect(ref, _sarif(with_invocation_pointer=True))
    assert overridden[0].severity == "high"

    # Without the provenance pointer, the rule defaultConfiguration ("note") applies -> low.
    defaulted = SarifObservationAdapter().collect(ref, _sarif(with_invocation_pointer=False))
    assert defaulted[0].severity == "low"
