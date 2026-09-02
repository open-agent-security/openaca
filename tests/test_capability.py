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


def test_capability_rejects_non_string_evidence_kind():
    import pytest

    with pytest.raises(ValueError):
        Capability(
            name="shell_exec",
            execution_locus="local",
            method="declared",
            source="openaca",
            source_version="0.4.0",
            confidence="high",
            evidence=[{"kind": 123}],  # type: ignore[dict-item]
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


def test_abs_path_launcher_matches_curated_seed():
    # A full-path launcher (`/usr/local/bin/npx`) must be normalized to its
    # basename so the npx package coordinate is derived, matching the seed.
    ref = ComponentRef(
        component_identity="mcp-server/fs",
        extra={
            "component_type": "mcp_server",
            "install_source": "/usr/local/bin/npx @modelcontextprotocol/server-filesystem",
        },
    )
    caps, _ = capabilities_for_ref(ref, load_capability_corpus())
    assert {c.name for c in caps} >= {"file_read", "file_write"}


def test_extensioned_launcher_matches_curated_seed():
    # A `.cmd` launcher spelling (classified as npx via stem) must still derive
    # the npx coordinate and match the seed.
    ref = ComponentRef(
        component_identity="mcp-server/fs",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx.cmd @modelcontextprotocol/server-filesystem",
        },
    )
    caps, _ = capabilities_for_ref(ref, load_capability_corpus())
    assert {c.name for c in caps} >= {"file_read", "file_write"}


def test_pypi_launch_name_is_normalized_before_matching(tmp_path):
    # A curated record keyed on the PEP 503-normalized PyPI coordinate must
    # match a launch that uses a non-normalized spelling (case + separators).
    (tmp_path / "rec.yaml").write_text(
        "identity: mcp-server/pypi/aws-mcp-server\n"
        "last_reviewed: '2026-07-03'\nreviewed_version: '1.0'\ncapabilities:\n"
        "  - {name: network_egress, execution_locus: remote, confidence: high,\n"
        "     evidence: [{kind: curated_review}]}\n"
    )
    corpus = load_capability_corpus(root=tmp_path)
    ref = ComponentRef(
        component_identity="mcp-server/aws",
        extra={"component_type": "mcp_server", "install_source": "uvx AWS_MCP_Server==1.0"},
    )
    caps, coverage = capabilities_for_ref(ref, corpus)
    assert {c.name for c in caps} == {"network_egress"}
    assert coverage == "partial"


def _skill_with(tmp_path, body):
    p = tmp_path / "SKILL.md"
    p.write_text(body)
    return ComponentRef(name="x", source_manifest=str(p), extra={"component_type": "skill"})


def test_covered_but_declaring_nothing_is_partial_not_unknown(tmp_path):
    """The distinction ADR-0041 principle 2 requires.

    `unknown` must mean "no mechanism could read this component", never "a
    mechanism read it and found none of the taxonomy". Deriving coverage from
    an empty result collapsed the two and left every silent component
    indistinguishable from an unreadable one, so no divergence rule could
    read a component's silence as evidence.
    """
    ref = _skill_with(tmp_path, "---\nname: x\nallowed-tools: TodoWrite\n---\n")
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert caps == [] and coverage == "partial"


def test_unreadable_skill_frontmatter_stays_unknown(tmp_path):
    """A failed read is uncovered, not covered-and-empty.

    The inverse error of the one above, and the worse of the two: it would
    claim OpenACA read a declaration it could not parse.
    """
    ref = _skill_with(tmp_path, "---\nname: x\nallowed-tools: [unclosed\n---\n")
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert caps == [] and coverage == "unknown"


def test_skill_without_allowed_tools_stays_unknown(tmp_path):
    ref = _skill_with(tmp_path, "---\nname: x\ndescription: y\n---\n")
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert caps == [] and coverage == "unknown"


def test_stdio_mcp_without_a_curated_record_stays_unknown():
    ref = ComponentRef(
        component_identity="mcp-server/unreviewed",
        extra={"component_type": "mcp_server", "install_source": "uvx unreviewed-server"},
    )
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert caps == [] and coverage == "unknown"


def test_remote_mcp_is_partial():
    ref = ComponentRef(
        component_identity="mcp-server/x",
        extra={"component_type": "mcp_server", "url": "https://mcp.example.com/mcp"},
    )
    caps, coverage = capabilities_for_ref(ref, load_capability_corpus())
    assert {c.name for c in caps} == {"network_egress", "sensitive_data_access"}
    assert coverage == "partial"


def test_a_curated_record_listing_nothing_still_covers(tmp_path):
    """A reviewed record is coverage even when it asserts no capability.

    Coverage tracks whether a mechanism ran, so it must key on the record
    existing rather than on the capabilities it happens to carry -- otherwise a
    reviewer who concludes "this server does none of these things" produces the
    same output as no review at all.
    """
    from tools.capability_corpus import load_capability_corpus as _load

    (tmp_path / "empty.yaml").write_text(
        'identity: mcp-server/npm/reviewed-inert\nlast_reviewed: "2026-01-01"\n'
        'reviewed_version: "1.0.0"\ncapabilities: []\n'
    )
    corpus = _load(tmp_path)
    ref = ComponentRef(
        component_identity="mcp-server/inert",
        extra={
            "component_type": "mcp_server",
            "install_source": "npx reviewed-inert",
        },
    )
    caps, coverage = capabilities_for_ref(ref, corpus)
    assert caps == [] and coverage == "partial"
