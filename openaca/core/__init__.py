"""`openaca.core` — the supported, curated domain surface for consumers.

This is a thin re-export layer over OpenACA's internal modules (currently under
`tools.*`). Downstream consumers depend on `openaca.core` and pin a version/SHA;
it is the cross-consumer consumption seam, **not a stable public API pre-V0**
(ADR-0028). Consumers must not import `tools.*` directly or reimplement these
semantics — identity, BOM parsing, OSV query planning, matching, severity
normalization, attribution, and exposure decisions are owned here.
"""

from openaca.core.bom import (
    BOMComponent,
    bom_components_from_cyclonedx,
    build_agent_bom,
    component_refs_from_cyclonedx,
)
from openaca.core.component_ref import ComponentRef
from openaca.core.exposure import (
    ExposureCard,
    ExposureComponent,
    ExposureDecision,
    ExposureEvidence,
    ExposureOccurrence,
    ExposurePathNode,
    build_exposure_cards,
    decide_exposure,
    render_triage_report,
)
from openaca.core.identity import MatchCoordinate, match_coordinates
from openaca.core.matching import Finding, match
from openaca.core.osv_queries import (
    OsvQuery,
    collect_osv_queries,
    stamp_osv_query_provenance,
)
from openaca.core.severity import derive_severity_label, derive_severity_score

__all__ = [
    "BOMComponent",
    "ComponentRef",
    "ExposureCard",
    "ExposureComponent",
    "ExposureDecision",
    "ExposureEvidence",
    "ExposureOccurrence",
    "ExposurePathNode",
    "Finding",
    "MatchCoordinate",
    "OsvQuery",
    "bom_components_from_cyclonedx",
    "build_agent_bom",
    "build_exposure_cards",
    "collect_osv_queries",
    "component_refs_from_cyclonedx",
    "derive_severity_label",
    "derive_severity_score",
    "decide_exposure",
    "match",
    "match_coordinates",
    "render_triage_report",
    "stamp_osv_query_provenance",
]
