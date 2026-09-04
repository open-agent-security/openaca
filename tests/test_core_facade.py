"""The openaca.core facade must re-export the exact same objects as tools.*.

This is the supported cross-consumer surface (ADR-0028). The test guards that
the facade stays a thin re-export — every symbol is identical to its source, so
a consumer importing openaca.core gets the real domain logic, not a copy.
"""

import subprocess
import sys
from pathlib import Path

import openaca.core as core
import tools.bom
import tools.collect
import tools.component_ref
import tools.identity
import tools.kind_selection
import tools.matcher
import tools.observations.finding
import tools.osv_federation
import tools.policy
import tools.policy_compile
import tools.posture.finding
import tools.severity


def test_facade_reexports_are_identical_objects():
    assert core.ComponentRef is tools.component_ref.ComponentRef
    assert core.MatchCoordinate is tools.identity.MatchCoordinate
    assert core.match_coordinates is tools.identity.match_coordinates
    assert core.BOMComponent is tools.bom.BOMComponent
    assert core.build_agent_bom is tools.bom.build_agent_bom
    assert core.component_refs_from_cyclonedx is tools.bom.component_refs_from_cyclonedx
    assert core.bom_components_from_cyclonedx is tools.bom.bom_components_from_cyclonedx
    assert core.graph_from_cyclonedx is tools.bom.graph_from_cyclonedx
    assert core.OsvQuery is tools.osv_federation.OsvQuery
    assert core.collect_osv_queries is tools.osv_federation.collect_osv_queries
    assert core.stamp_osv_query_provenance is tools.osv_federation.stamp_osv_query_provenance
    assert core.match is tools.matcher.match
    assert core.Finding is tools.matcher.Finding
    assert core.derive_severity_label is tools.severity.derive_severity_label
    assert core.derive_severity_score is tools.severity.derive_severity_score
    assert core.Policy is tools.policy.Policy
    assert core.PolicyValidationError is tools.policy.PolicyValidationError
    assert core.PolicyEvaluationError is tools.policy.PolicyEvaluationError
    assert core.EndpointComponent is tools.policy.EndpointComponent
    assert core.Decision is tools.policy.Decision
    assert core.parse_policy is tools.policy.parse
    assert core.parse_policy_source is tools.policy.loads
    assert core.evaluate_admission is tools.policy.evaluate_admission
    assert core.apply_risk_gates is tools.policy.apply_risk_gates


def test_install_source_helpers_are_reexported_from_identity():
    """The three install-source safety helpers a consumer needs to trim an
    install source without reimplementing OpenACA's identity semantics.

    `tools/component_ref.py` re-exports `safe_pinned_mcp_install_source` too;
    the facade re-exports from `tools/identity.py`, which is where all three
    are defined, so the identity module stays the single implementation.
    """
    assert (
        core.is_mcp_package_launch_install_source
        is tools.identity.is_mcp_package_launch_install_source
    )
    assert core.safe_unpinned_mcp_install_source is tools.identity.safe_unpinned_mcp_install_source
    assert core.safe_pinned_mcp_install_source is tools.identity.safe_pinned_mcp_install_source


def test_finding_value_types_are_reexported():
    """`collect_installed_agents` returns these as themselves rather than as
    payload dictionaries, so the result has somewhere to point."""
    assert core.PostureFinding is tools.posture.finding.PostureFinding
    assert core.Standards is tools.posture.finding.Standards
    assert core.ObservationFinding is tools.observations.finding.ObservationFinding


def test_collection_api_is_reexported():
    assert core.collect_installed_agents is tools.collect.collect_installed_agents
    assert core.CollectedAgent is tools.collect.CollectedAgent
    assert core.ScannerUnavailable is tools.collect.ScannerUnavailable


# The surface is defined as much by what is absent as by what is present, and
# absence is exactly what rots without a test.
_INTERNAL_TO_COLLECTION = (
    # symbol, why it stays in
    ("discover_agents", "discovery is the collection function's first step"),
    ("DiscoveryContext", "a discovery input"),
    ("AgentInstance", "a discovery intermediate"),
    ("kind_for", "which kinds exist and how they resolve surfaces"),
    ("REGISTRY", "the kind registry"),
    ("build_agent_graph", "a construction detail of the BOM"),
    ("Graph", "a construction detail of the BOM"),
    ("WarningLog", "the result carries plain strings instead"),
    ("resolve_coverage", "applied to the BOM before it is returned"),
    ("_component_gap_count", "called internally; stays private in tools/scan.py"),
    ("_count_active_plugins", "called internally; stays private in tools/scan.py"),
    ("run_posture_rules", "an implementation of running posture rules"),
    ("no_manifests", "a collector-pair default for kinds with no surface"),
    ("agent_posture_manifests", "per-kind posture manifest resolution"),
    ("agent_extra_posture_manifests", "per-kind posture manifest resolution"),
    ("collect_skill_observations", "observation collection"),
    ("collect_skillspector_findings", "observation collection"),
)


def test_the_machinery_behind_collection_is_not_reachable_through_the_facade():
    """Seventeen symbols a consumer would otherwise assemble by hand, reduced to
    five public names with zero private ones promoted."""
    for name, why in _INTERNAL_TO_COLLECTION:
        assert not hasattr(core, name), f"{name} leaked onto the facade ({why})"
        assert name not in core.__all__, f"{name} leaked into __all__ ({why})"


def test_the_two_scan_counters_stay_private():
    """Calling them internally is what stops them being a problem. A later
    tidy-up that renames them public reintroduces it."""
    import tools.scan

    assert hasattr(tools.scan, "_component_gap_count")
    assert hasattr(tools.scan, "_count_active_plugins")
    assert not hasattr(tools.scan, "component_gap_count")
    assert not hasattr(tools.scan, "count_active_plugins")


def test_kind_selection_validation_is_reexported():
    """The check is published; the facts it checks are not — `REGISTRY` and
    `kind_for` are asserted absent above. A consumer given the facts would
    rebuild the validation and phrase its own errors, and the two wordings
    would drift while both claimed to describe the same rule."""
    assert core.validate_kind_selection is tools.kind_selection.validate_kind_selection
    assert core.KindSelectionError is tools.kind_selection.KindSelectionError


def test_the_kind_selection_error_is_catchable_by_name(tmp_path):
    """A caller must be able to name the failure without catching `Exception`,
    which is the point of not importing internals."""
    try:
        core.validate_kind_selection(None, tmp_path)
    except core.KindSelectionError as exc:
        assert "--config-dir requires --kind" in str(exc)
    else:
        raise AssertionError("a config root without a kind must not validate")


def test_policy_compilation_is_reexported():
    assert core.compile_endpoint_policy is tools.policy_compile.compile_endpoint_policy
    assert core.render_policy_report is tools.policy_compile.render_policy_report


def test_importing_the_facade_does_not_import_the_command_modules():
    """The three modules the spec names, asserted in a fresh interpreter so
    `sys.modules` is clean.

    What this does *not* claim: that the facade avoids `click`. `openaca.core`
    already pulls `click` in through another path today and the spec says so
    explicitly — this guards module layering, not the dependency. `tools/scan.py`
    is the known, documented exception: it declares the `scan` command group and
    the compilation imports four private helpers from it.
    """
    forbidden = ("tools.policy_cli", "tools.bom_cli", "tools.cli")
    script = f"import sys, openaca.core; print([m for m in {forbidden!r} if m in sys.modules])"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parent.parent,
    )

    assert result.stdout.strip() == "[]", result.stdout
