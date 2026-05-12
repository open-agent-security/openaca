"""Tests for `tools.severity.derive_severity_label` / `derive_severity_score`.

Validates the precedence rule:
  1. `database_specific.severity` (upstream label) wins
  2. Compute from `severity[].score` (CVSS vector)
  3. UNKNOWN
"""

from __future__ import annotations

import pytest

from tools.severity import derive_severity_label, derive_severity_score


def _advisory(**fields) -> dict:
    base = {
        "id": "TEST-0001",
        "schema_version": "1.7.5",
        "type": "vulnerability",
        "summary": "test",
        "details": "test",
    }
    base.update(fields)
    return base


def test_upstream_label_wins_when_present():
    """Federated GHSA records carry `database_specific.severity`; we respect
    that label as the upstream authority's judgment, even if a CVSS vector
    is also present and would compute differently."""
    advisory = _advisory(
        database_specific={"severity": "HIGH"},
        severity=[
            {
                "type": "CVSS_V3",
                # This vector computes to 1.8 (LOW), but the upstream label wins.
                "score": "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N",
            }
        ],
    )
    assert derive_severity_label(advisory) == "HIGH"


def test_ghsa_moderate_normalizes_to_medium():
    """GHSA uses `MODERATE`; FIRST's standard label is `MEDIUM`. Normalize
    so downstream renderers only have to handle one vocabulary."""
    advisory = _advisory(database_specific={"severity": "moderate"})
    assert derive_severity_label(advisory) == "MEDIUM"


def test_computed_from_v3_vector_when_no_upstream_label():
    advisory = _advisory(
        severity=[{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]
    )
    assert derive_severity_label(advisory) == "CRITICAL"


def test_computed_from_v4_vector_when_no_upstream_label():
    advisory = _advisory(
        severity=[
            {
                "type": "CVSS_V4",
                "score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
            }
        ]
    )
    assert derive_severity_label(advisory) == "CRITICAL"


def test_unknown_when_no_severity_info():
    assert derive_severity_label(_advisory()) == "UNKNOWN"


def test_unknown_when_severity_block_malformed():
    """Various malformed shapes shouldn't crash — return UNKNOWN."""
    assert derive_severity_label(_advisory(severity="not a list")) == "UNKNOWN"
    assert derive_severity_label(_advisory(severity=[None, 1, "x"])) == "UNKNOWN"
    assert (
        derive_severity_label(_advisory(severity=[{"type": "CVSS_V3", "score": "garbage"}]))
        == "UNKNOWN"
    )


def test_unknown_label_string_falls_through_to_compute():
    """Unknown upstream label values fall through; the CVSS path takes a shot."""
    advisory = _advisory(
        database_specific={"severity": "PERHAPS"},
        severity=[{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H"}],
    )
    assert derive_severity_label(advisory) == "HIGH"


def test_first_parseable_severity_wins():
    """If multiple severity entries exist, take the first one that parses.
    Matches OSV-schema consumer convention."""
    advisory = _advisory(
        severity=[
            {"type": "BOGUS", "score": "whatever"},
            {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H"},
            {
                "type": "CVSS_V4",
                "score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
            },
        ]
    )
    # First parseable (the v3 7.2) wins, not the v4 9.3.
    assert derive_severity_label(advisory) == "HIGH"


def test_derive_score_returns_number_from_vector():
    advisory = _advisory(
        severity=[{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H"}]
    )
    score = derive_severity_score(advisory)
    assert score is not None
    assert score == pytest.approx(7.2, abs=0.05)


def test_derive_score_returns_none_when_only_upstream_label():
    """Upstream qualitative labels can't be back-derived into a numeric
    score — return None so the JSON renderer doesn't synthesize a fake one."""
    advisory = _advisory(database_specific={"severity": "HIGH"})
    assert derive_severity_score(advisory) is None


def test_derive_score_returns_none_when_upstream_label_wins_over_cvss():
    """When upstream label wins, score is None even if a CVSS vector is
    present. Prevents emitting a score that contradicts the label (e.g.
    label=HIGH but score=1.8 from a low vector)."""
    advisory = _advisory(
        database_specific={"severity": "HIGH"},
        severity=[
            {
                "type": "CVSS_V3",
                # This vector computes to ~1.8 (LOW) — must not leak through.
                "score": "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N",
            }
        ],
    )
    assert derive_severity_score(advisory) is None


def test_derive_score_returns_none_for_unknown_advisory():
    assert derive_severity_score(_advisory()) is None
