"""CVSS v3 / v4 vector validators and base-score helpers.

Validation enforces required base-metric grammar for the declared version.
v3.0 and v3.1 share the same base-metric set; we accept both prefixes
through a single v3 validator (the differences are scoring math, not
grammar).

Numeric base-score computation is delegated to the `cvss` PyPI library —
v4 scoring requires a ~270-entry macrovector lookup table published in
FIRST's spec, and re-implementing it ourselves would carry maintenance
cost for no upside. `cvss` is a small, vetted, pure-Python library.

`severity_label(score)` maps a numeric base score to the standardized
qualitative label (None/Low/Medium/High/Critical) per FIRST's mapping
table; this matches what GHSA / NVD / OSV consumers expect to see.
"""

from __future__ import annotations

from typing import Optional

from cvss import CVSS3, CVSS4
from cvss.exceptions import CVSSError

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
    if metrics.keys() - required.keys():
        return False  # non-base metric present; base-only policy
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


def score_v3(vector: str) -> Optional[float]:
    """Return the CVSS v3.x base score for `vector`, or None if invalid.

    Accepts both `CVSS:3.0/` and `CVSS:3.1/` prefixes. Returns None on
    parse errors so callers don't have to guard separately — useful when
    advisory data has been tampered with or carries non-base extensions
    the upstream `cvss` library rejects.
    """
    if not is_valid_cvss_v3(vector):
        return None
    try:
        score = CVSS3(vector).base_score
    except (CVSSError, ValueError):
        return None
    if score is None:
        return None
    return float(score)


def score_v4(vector: str) -> Optional[float]:
    """Return the CVSS v4.0 base score for `vector`, or None if invalid."""
    if not is_valid_cvss_v4(vector):
        return None
    try:
        score = CVSS4(vector).base_score
    except (CVSSError, ValueError):
        return None
    if score is None:
        return None
    return float(score)


def severity_label(score: Optional[float]) -> str:
    """Map a numeric base score to the FIRST-standard qualitative label.

    Thresholds per FIRST CVSS v3.1 §5 / v4.0 §3 (identical across versions):
    - 0.0:        NONE
    - 0.1 – 3.9:  LOW
    - 4.0 – 6.9:  MEDIUM
    - 7.0 – 8.9:  HIGH
    - 9.0 – 10.0: CRITICAL

    Returns `"UNKNOWN"` when `score` is None (no parseable vector).
    """
    if score is None:
        return "UNKNOWN"
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    if score > 0.0:
        return "LOW"
    return "NONE"
