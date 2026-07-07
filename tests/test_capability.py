from tools.capability import (
    CAPABILITY_NAMES,
    COVERAGE_LEVELS,
    Capability,
    capabilities_for_ref,
)
from tools.capability_corpus import load_capability_corpus
from tools.component_ref import ComponentRef


def test_taxonomy_is_closed():
    assert CAPABILITY_NAMES == frozenset(
        {
            "file_read",
            "file_write",
            "shell_exec",
            "network_egress",
            "credential_access",
            "sensitive_data_access",
        }
    )
    assert COVERAGE_LEVELS == ("unknown", "partial", "complete")


def test_capability_roundtrip():
    cap = Capability(
        name="shell_exec",
        execution_locus="local",
        method="declared",
        source="openaca",
        source_version="0.4.0",
        confidence="high",
        evidence=[
            {
                "kind": "manifest_field",
                "path": "SKILL.md",
                "field": "allowed-tools",
                "value": "Bash(*)",
            }
        ],
    )
    assert Capability.from_dict(cap.to_dict()) == cap


def test_capability_requires_nonempty_evidence():
    import pytest

    with pytest.raises(ValueError):
        Capability(
            name="shell_exec",
            execution_locus="local",
            method="declared",
            source="openaca",
            source_version="0.4.0",
            confidence="high",
            evidence=[],
        )


def test_capability_rejects_non_object_evidence_entry():
    import pytest

    with pytest.raises(ValueError):
        Capability(
            name="shell_exec",
            execution_locus="local",
            method="declared",
            source="openaca",
            source_version="0.4.0",
            confidence="high",
            evidence=[42],  # type: ignore[list-item]
        )


def test_capability_rejects_evidence_entry_missing_kind():
    import pytest

    with pytest.raises(ValueError):
        Capability(
            name="shell_exec",
            execution_locus="local",
            method="declared",
            source="openaca",
            source_version="0.4.0",
            confidence="high",
            evidence=[{"path": "SKILL.md"}],
        )


def _skill_with_bash(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: x\nallowed-tools: Bash(*)\n---\n")
    return ComponentRef(name="x", source_manifest=str(p), extra={"component_type": "skill"})


def test_merges_declared_and_curated_sets_partial(tmp_path):
    ref = _skill_with_bash(tmp_path)
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert {c.name for c in caps} >= {"shell_exec"}
    assert coverage == "partial"


def test_no_signal_is_unknown():
    caps, coverage = capabilities_for_ref(
        ComponentRef(name="p", extra={"component_type": "plugin"}),
        load_capability_corpus(),
    )
    assert caps == [] and coverage == "unknown"


def test_package_mcp_matches_curated_seed_despite_local_alias():
    ref = ComponentRef(
        component_identity="mcp-server/fs",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx @modelcontextprotocol/server-filesystem",
        },
    )
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert {c.name for c in caps} >= {"file_read", "file_write"}
    assert coverage == "partial"


def test_pinned_launch_matches_unpinned_seed_coordinate():
    ref = ComponentRef(
        component_identity="mcp-server/fs",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx @modelcontextprotocol/server-filesystem@1.2.3",
        },
    )
    caps, _ = capabilities_for_ref(ref, load_capability_corpus())
    assert {c.name for c in caps} >= {"file_read", "file_write"}


def test_git_launch_source_yields_no_coordinate():
    ref = ComponentRef(
        component_identity="mcp-server/x",
        extra={
            "component_type": "mcp_server",
            "install_source": "uvx git+https://github.com/org/repo",
        },
    )
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert caps == [] and coverage == "unknown"
