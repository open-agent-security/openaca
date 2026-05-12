"""Minimal CVSS v3 / v4 vector validators.

Verifies the vector matches the required base-metric grammar for the
declared version. Does not compute numeric scores — that's a future
concern. v3.0 and v3.1 share the same base-metric set; we accept both
prefixes through a single v3 validator (the differences are scoring
math, not grammar).
"""

from __future__ import annotations

REQUIRED_BASE_METRICS = {
    "AV": {"N", "A", "L", "P"},
    "AC": {"L", "H"},
    "AT": {"N", "P"},
    "PR": {"N", "L", "H"},
    "UI": {"N", "P", "A"},
    "VC": {"H", "L", "N"},
    "VI": {"H", "L", "N"},
    "VA": {"H", "L", "N"},
    "SC": {"H", "L", "N"},
    "SI": {"H", "L", "N"},
    "SA": {"H", "L", "N"},
}

_V3_BASE_METRICS = {
    "AV": {"N", "A", "L", "P"},
    "AC": {"L", "H"},
    "PR": {"N", "L", "H"},
    "UI": {"N", "R"},
    "S": {"U", "C"},
    "C": {"N", "L", "H"},
    "I": {"N", "L", "H"},
    "A": {"N", "L", "H"},
}

_V3_PREFIXES = ("CVSS:3.0/", "CVSS:3.1/")
_V4_PREFIX = "CVSS:4.0/"


def _validate_vector(vector: str, prefix: str, required: dict[str, set[str]]) -> bool:
    if not vector.startswith(prefix):
        return False
    parts = vector[len(prefix) :].split("/")
    metrics: dict[str, str] = {}
    for part in parts:
        if ":" not in part:
            return False
        key, value = part.split(":", 1)
        if key in metrics:
            return False  # duplicate metric
        metrics[key] = value
    for key, allowed in required.items():
        if key not in metrics or metrics[key] not in allowed:
            return False
    return True


def is_valid_cvss_v4(vector: str) -> bool:
    return _validate_vector(vector, _V4_PREFIX, REQUIRED_BASE_METRICS)


def is_valid_cvss_v3(vector: str) -> bool:
    """Accepts both CVSS 3.0 and 3.1; their base-metric grammar is identical."""
    for prefix in _V3_PREFIXES:
        if vector.startswith(prefix):
            return _validate_vector(vector, prefix, _V3_BASE_METRICS)
    return False


def is_valid_cvss(severity_type: str, vector: str) -> bool:
    """Dispatch to the right validator. Returns False for unknown types.

    Enforces type/vector agreement: a `CVSS_V3` declaration with a
    `CVSS:4.0/...` body fails here even though the v4 grammar would
    accept the body in isolation.
    """
    if severity_type == "CVSS_V4":
        return is_valid_cvss_v4(vector)
    if severity_type == "CVSS_V3":
        return is_valid_cvss_v3(vector)
    return False
