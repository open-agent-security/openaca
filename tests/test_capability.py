from tools.capability import CAPABILITY_NAMES, COVERAGE_LEVELS, Capability


def test_taxonomy_is_closed():
    assert CAPABILITY_NAMES == frozenset({
        "file_read", "file_write", "shell_exec",
        "network_egress", "credential_access", "sensitive_data_access",
    })
    assert COVERAGE_LEVELS == ("unknown", "partial", "complete")


def test_capability_roundtrip():
    cap = Capability(
        name="shell_exec", execution_locus="local", method="declared",
        source="openaca", source_version="0.4.0", confidence="high",
        evidence=[{"kind": "manifest_field", "path": "SKILL.md",
                   "field": "allowed-tools", "value": "Bash(*)"}],
    )
    assert Capability.from_dict(cap.to_dict()) == cap


def test_capability_requires_nonempty_evidence():
    import pytest
    with pytest.raises(ValueError):
        Capability(name="shell_exec", execution_locus="local", method="declared",
                   source="openaca", source_version="0.4.0", confidence="high",
                   evidence=[])
