"""Minimal CVSS v4 vector validator.

Verifies the vector matches the v4 grammar for required base metrics.
Does not compute scores; that's a future concern.
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


def is_valid_cvss_v4(vector: str) -> bool:
    if not vector.startswith("CVSS:4.0/"):
        return False
    parts = vector[len("CVSS:4.0/"):].split("/")
    metrics: dict[str, str] = {}
    for part in parts:
        if ":" not in part:
            return False
        key, value = part.split(":", 1)
        metrics[key] = value
    for key, allowed in REQUIRED_BASE_METRICS.items():
        if key not in metrics or metrics[key] not in allowed:
            return False
    return True
