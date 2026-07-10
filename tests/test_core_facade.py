"""The openaca.core facade must re-export the exact same objects as tools.*.

This is the supported cross-consumer surface (ADR-0028). The test guards that
the facade stays a thin re-export — every symbol is identical to its source, so
a consumer importing openaca.core gets the real domain logic, not a copy.
"""

import openaca.core as core
import tools.bom
import tools.component_ref
import tools.identity
import tools.matcher
import tools.osv_federation
import tools.severity
import tools.triage
import tools.triage_render


def test_facade_reexports_are_identical_objects():
    assert core.ComponentRef is tools.component_ref.ComponentRef
    assert core.MatchCoordinate is tools.identity.MatchCoordinate
    assert core.match_coordinates is tools.identity.match_coordinates
    assert core.BOMComponent is tools.bom.BOMComponent
    assert core.build_agent_bom is tools.bom.build_agent_bom
    assert core.component_refs_from_cyclonedx is tools.bom.component_refs_from_cyclonedx
    assert core.bom_components_from_cyclonedx is tools.bom.bom_components_from_cyclonedx
    assert core.OsvQuery is tools.osv_federation.OsvQuery
    assert core.collect_osv_queries is tools.osv_federation.collect_osv_queries
    assert core.stamp_osv_query_provenance is tools.osv_federation.stamp_osv_query_provenance
    assert core.match is tools.matcher.match
    assert core.Finding is tools.matcher.Finding
    assert core.derive_severity_label is tools.severity.derive_severity_label
    assert core.derive_severity_score is tools.severity.derive_severity_score
    assert core.ExposureCard is tools.triage.ExposureCard
    assert core.ExposureComponent is tools.triage.ExposureComponent
    assert core.ExposureDecision is tools.triage.ExposureDecision
    assert core.ExposureEvidence is tools.triage.ExposureEvidence
    assert core.ExposureOccurrence is tools.triage.ExposureOccurrence
    assert core.ExposurePathNode is tools.triage.ExposurePathNode
    assert core.build_exposure_cards is tools.triage.build_exposure_cards
    assert core.decide_exposure is tools.triage.decide_exposure
    assert core.render_triage_report is tools.triage_render.render_triage_report
