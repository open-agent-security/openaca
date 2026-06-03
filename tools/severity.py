"""Derive a severity label and numeric score for an advisory dict.

Precedence (agreed in plan-mode discussion):

1. **Upstream label** — if `database_specific.severity` is a non-empty
   string, normalize and return. GHSA records always populate this field
   with the GHSA editor's qualitative judgment, and respecting that label
   for federated records preserves the upstream authority's call.

2. **Computed from CVSS vector** — iterate `advisory.severity[]` and
   compute the base score for the first entry whose declared `type`
   matches a known CVSS version and whose `score` parses.

3. **UNKNOWN** — neither path produced a label.

The numeric-score helper follows the same precedence but only the CVSS
path produces a number; upstream labels alone can't be back-derived into
a score, so `derive_severity_score` returns None when the upstream-label
path is the only one available.
"""

from __future__ import annotations

from typing import Any, Optional

from tools.cvss import score_v3, score_v4, severity_label

_LABEL_ALIASES = {
    "NONE": "NONE",
    "LOW": "LOW",
    "MODERATE": "MEDIUM",  # GHSA uses MODERATE; we normalize to FIRST's MEDIUM
    "MEDIUM": "MEDIUM",
    "HIGH": "HIGH",
    "CRITICAL": "CRITICAL",
}


def _upstream_label(advisory: dict[str, Any]) -> Optional[str]:
    """Read `database_specific.severity` if present and non-empty.

    Normalize GHSA's `MODERATE` to FIRST's `MEDIUM` so the rest of the
    pipeline only has to handle one vocabulary. Unknown labels fall
    through so the CVSS path can take a shot.
    """
    ds = advisory.get("database_specific")
    if not isinstance(ds, dict):
        return None
    raw = ds.get("severity")
    if not isinstance(raw, str) or not raw.strip():
        return None
    normalized = _LABEL_ALIASES.get(raw.strip().upper())
    return normalized


def _computed_score_and_label(advisory: dict[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """Return the first parseable (score, label) from advisory.severity[].

    Iterates in order; the first entry whose `type` is known and whose
    `score` parses wins. This mirrors how OSV-schema consumers usually
    treat the array: first applicable severity.
    """
    severity_list = advisory.get("severity")
    if not isinstance(severity_list, list):
        return None, None
    for entry in severity_list:
        if not isinstance(entry, dict):
            continue
        sev_type = entry.get("type")
        vector = entry.get("score")
        if not isinstance(vector, str):
            continue
        if sev_type == "CVSS_V3":
            score = score_v3(vector)
        elif sev_type == "CVSS_V4":
            score = score_v4(vector)
        else:
            continue
        if score is None:
            continue
        return score, severity_label(score)
    return None, None


def derive_severity_label(advisory: dict[str, Any]) -> str:
    """Return the canonical severity label for `advisory`.

    Upstream `database_specific.severity` wins when present (preserves
    the federated authority's judgment). Otherwise compute from the
    CVSS vector. Returns "UNKNOWN" when both paths fail.
    """
    upstream = _upstream_label(advisory)
    if upstream is not None:
        return upstream
    _score, label = _computed_score_and_label(advisory)
    return label if label is not None else "UNKNOWN"


def derive_severity_score(advisory: dict[str, Any]) -> Optional[float]:
    """Return the numeric CVSS base score for `advisory`, or None.

    Follows the same precedence as `derive_severity_label`: when
    `database_specific.severity` is present, the upstream label wins and
    this returns None (qualitative labels can't be back-derived into a
    number). The CVSS path only runs when no upstream label is available.
    """
    if _upstream_label(advisory) is not None:
        return None
    score, _label = _computed_score_and_label(advisory)
    return score
