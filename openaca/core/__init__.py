"""`openaca.core` — the supported, curated domain surface for consumers.

This is a thin re-export layer over OpenACA's internal modules (currently under
`tools.*`). Downstream consumers depend on `openaca.core` and pin a version/SHA;
it is the cross-consumer consumption seam, **not a stable public API pre-V0**
(ADR-0028). Consumers must not import `tools.*` directly or reimplement these
semantics — identity, BOM parsing, OSV query planning, matching, severity
normalization, and attribution are owned here.
"""

from openaca.core.bom import (
    BOMComponent,
    bom_components_from_cyclonedx,
    build_agent_bom,
    component_refs_from_cyclonedx,
    graph_from_cyclonedx,
)
from openaca.core.collect import (
    CollectedAgent,
    ScannerUnavailable,
    collect_installed_agents,
)
from openaca.core.component_ref import ComponentRef
from openaca.core.findings import ObservationFinding, PostureFinding, Standards
from openaca.core.identity import (
    MatchCoordinate,
    is_mcp_package_launch_install_source,
    match_coordinates,
    safe_pinned_mcp_install_source,
    safe_unpinned_mcp_install_source,
)
from openaca.core.kind_selection import KindSelectionError, validate_kind_selection
from openaca.core.matching import Finding, match
from openaca.core.osv_queries import (
    OsvQuery,
    collect_osv_queries,
    stamp_osv_query_provenance,
)
from openaca.core.policy import (
    Decision,
    EndpointComponent,
    Policy,
    PolicyEvaluationError,
    PolicyValidationError,
    apply_risk_gates,
    compile_endpoint_policy,
    evaluate_admission,
    parse_policy,
    parse_policy_source,
    render_policy_report,
)
from openaca.core.severity import derive_severity_label, derive_severity_score

__all__ = [
    "BOMComponent",
    "CollectedAgent",
    "ComponentRef",
    "Decision",
    "EndpointComponent",
    "Finding",
    "KindSelectionError",
    "MatchCoordinate",
    "ObservationFinding",
    "OsvQuery",
    "Policy",
    "PolicyEvaluationError",
    "PolicyValidationError",
    "PostureFinding",
    "ScannerUnavailable",
    "Standards",
    "apply_risk_gates",
    "bom_components_from_cyclonedx",
    "build_agent_bom",
    "collect_installed_agents",
    "collect_osv_queries",
    "compile_endpoint_policy",
    "component_refs_from_cyclonedx",
    "derive_severity_label",
    "derive_severity_score",
    "evaluate_admission",
    "graph_from_cyclonedx",
    "is_mcp_package_launch_install_source",
    "match",
    "match_coordinates",
    "parse_policy",
    "parse_policy_source",
    "render_policy_report",
    "safe_pinned_mcp_install_source",
    "safe_unpinned_mcp_install_source",
    "stamp_osv_query_provenance",
    "validate_kind_selection",
]
