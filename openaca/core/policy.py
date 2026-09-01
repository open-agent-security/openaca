"""Facade re-export: policy validation and evaluation. See ADR-0028."""

from tools.policy import (
    Decision,
    EndpointComponent,
    Policy,
    PolicyEvaluationError,
    PolicyValidationError,
    apply_risk_gates,
    evaluate_admission,
    loads,
    parse,
)

parse_policy = parse
parse_policy_source = loads

__all__ = [
    "Decision",
    "EndpointComponent",
    "Policy",
    "PolicyEvaluationError",
    "PolicyValidationError",
    "apply_risk_gates",
    "evaluate_admission",
    "parse_policy",
    "parse_policy_source",
]
