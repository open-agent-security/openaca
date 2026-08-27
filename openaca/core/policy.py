"""Facade re-export: policy validation and evaluation. See ADR-0028."""

from tools.policy import (
    Decision,
    EndpointComponent,
    Policy,
    PolicyEvaluationError,
    PolicyValidationError,
    apply_risk_gates,
    evaluate_admission,
    parse,
)

parse_policy = parse

__all__ = [
    "Decision",
    "EndpointComponent",
    "Policy",
    "PolicyEvaluationError",
    "PolicyValidationError",
    "apply_risk_gates",
    "evaluate_admission",
    "parse_policy",
]
