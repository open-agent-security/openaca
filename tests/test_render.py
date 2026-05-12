"""Tests for the output renderers in `tools/render.py`.

Three renderers (text, github, json) consume the same (findings,
advisory_index, stats) inputs. These tests cover the shape contract
each renderer makes with its consumer.
"""

from __future__ import annotations

import json

import pytest

from tools.component_ref import ComponentRef
from tools.matcher import Finding
from tools.render import (
    ScanStats,
    _aggregate_fix,
    _fixed_in_for_finding,
    _group_findings,
    render_github,
    render_json,
    render_text,
)

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _ref(name: str, version: str, manifest: str = "pkg.json", **kw) -> ComponentRef:
    return ComponentRef(
        ecosystem=kw.get("ecosystem", "npm"),
        name=name,
        version=version,
        source_manifest=manifest,
        attributed_to=kw.get("attributed_to"),
    )


def _advisory(
    advisory_id: str,
    ecosystem: str,
    pkg: str,
    fixed: str | None = "9.9.9",
    summary: str = "test summary",
    severity_label: str | None = None,
    severity_vector: str | None = None,
    source: str = "asve.dev",
) -> dict:
    events: list[dict] = [{"introduced": "0"}]
    if fixed is not None:
        events.append({"fixed": fixed})
    out: dict = {
        "id": advisory_id,
        "summary": summary,
        "affected": [
            {
                "package": {"ecosystem": ecosystem, "name": pkg},
                "ranges": [{"type": "ECOSYSTEM", "events": events}],
            }
        ],
        "database_specific": {"asve": {"source": source}},
    }
    if severity_label is not None:
        out["database_specific"]["severity"] = severity_label
    if severity_vector is not None:
        # severity_vector is a CVSS:3.1 or CVSS:4.0 string.
        cvss_type = "CVSS_V3" if severity_vector.startswith("CVSS:3") else "CVSS_V4"
        out["severity"] = [{"type": cvss_type, "score": severity_vector}]
    return out


def _finding(
    advisory_id: str,
    name: str,
    version: str,
    confidence: str = "high",
    attributed_to: str | None = None,
    manifest: str = "pkg.json",
    ecosystem: str = "npm",
) -> Finding:
    return Finding(
        advisory_id=advisory_id,
        component=_ref(name, version, manifest, ecosystem=ecosystem, attributed_to=attributed_to),
        confidence=confidence,
        reason=f"{name}@{version} matches {advisory_id}",
        attributed_to=attributed_to,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def test_group_findings_collapses_same_component():
    """Two advisories on the same component → one group of two findings."""
    findings = [
        _finding("GHSA-A", "urllib3", "2.6.3"),
        _finding("GHSA-B", "urllib3", "2.6.3"),
    ]
    groups = _group_findings(findings)
    assert len(groups) == 1
    (only_group,) = groups.values()
    assert {f.advisory_id for f in only_group} == {"GHSA-A", "GHSA-B"}


def test_group_findings_separates_different_versions():
    """Same package at different versions are distinct groups (different units
    of remediation)."""
    findings = [
        _finding("GHSA-A", "urllib3", "2.6.3"),
        _finding("GHSA-A", "urllib3", "1.9.0"),
    ]
    groups = _group_findings(findings)
    assert len(groups) == 2


def test_fixed_in_extracts_first_fixed_event():
    advisory = _advisory("X", "npm", "urllib3", fixed="2.7.0")
    finding = _finding("X", "urllib3", "2.6.3")
    assert _fixed_in_for_finding(finding, advisory) == "2.7.0"


def test_fixed_in_returns_none_when_no_match():
    advisory = _advisory("X", "PyPI", "different-pkg", fixed="1.0.0")
    finding = _finding("X", "urllib3", "2.6.3")
    assert _fixed_in_for_finding(finding, advisory) is None


def test_fixed_in_selects_matched_window():
    """Multi-window advisory: returns the fix for the window the version falls in.

    Window 1: [0, 1.5.0) — fixed at 1.5.0
    Window 2: [2.0.0, 2.3.0) — fixed at 2.3.0

    A component at 2.1.0 is in window 2; returning 1.5.0 (window 1's fix, the
    first fixed event encountered) would be wrong — 2.1.0 > 1.5.0 and is still
    vulnerable.
    """
    advisory = {
        "id": "MULTI",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "lib"},
                "ranges": [
                    {
                        "type": "ECOSYSTEM",
                        "events": [
                            {"introduced": "0"},
                            {"fixed": "1.5.0"},
                            {"introduced": "2.0.0"},
                            {"fixed": "2.3.0"},
                        ],
                    }
                ],
            }
        ],
    }
    assert _fixed_in_for_finding(_finding("MULTI", "lib", "2.1.0"), advisory) == "2.3.0"
    assert _fixed_in_for_finding(_finding("MULTI", "lib", "1.2.0"), advisory) == "1.5.0"


def test_fixed_in_falls_back_to_raw_string_for_non_pep440_boundary():
    """Non-PEP 440 fixed version (e.g. npm prerelease) returns the raw string.

    The per-finding row shows "fixed in 1.0.0-beta.1" rather than "fixed in no
    fix", so users see that a fix exists even though version comparison is
    impossible. Note that _aggregate_fix still returns None for such strings
    (can't compute max), so the group fix: header correctly shows "see findings".
    """
    advisory = {
        "id": "GHSA-PREREL",
        "affected": [
            {
                "package": {"ecosystem": "npm", "name": "mylib"},
                "ranges": [
                    {
                        "type": "SEMVER",
                        "events": [
                            {"introduced": "0"},
                            {"fixed": "1.0.0-beta.1"},
                        ],
                    }
                ],
            }
        ],
    }
    assert (
        _fixed_in_for_finding(_finding("GHSA-PREREL", "mylib", "0.9.0"), advisory) == "1.0.0-beta.1"
    )


def test_aggregate_fix_picks_max():
    """Two findings, fixed in 2.6.4 and 2.7.0 → group fix is 2.7.0 (the
    smallest version that clears BOTH)."""
    fa = _finding("A", "urllib3", "2.6.3")
    fb = _finding("B", "urllib3", "2.6.3")
    index = {
        "A": _advisory("A", "npm", "urllib3", fixed="2.6.4"),
        "B": _advisory("B", "npm", "urllib3", fixed="2.7.0"),
    }
    assert _aggregate_fix([fa, fb], index) == "2.7.0"


def test_aggregate_fix_returns_none_when_any_missing():
    fa = _finding("A", "urllib3", "2.6.3")
    fb = _finding("B", "urllib3", "2.6.3")
    index = {
        "A": _advisory("A", "npm", "urllib3", fixed="2.7.0"),
        "B": _advisory("B", "npm", "urllib3", fixed=None),  # unpatched
    }
    assert _aggregate_fix([fa, fb], index) is None


def test_aggregate_fix_returns_none_on_unparseable_version():
    fa = _finding("A", "urllib3", "2.6.3")
    fb = _finding("B", "urllib3", "2.6.3")
    index = {
        "A": _advisory("A", "npm", "urllib3", fixed="not-a-version"),
        "B": _advisory("B", "npm", "urllib3", fixed="2.7.0"),
    }
    assert _aggregate_fix([fa, fb], index) is None


# ── render_text ──────────────────────────────────────────────────────────────


def _stats(unit_count=1, components=1, label="manifest", sources=("asve.dev",)) -> ScanStats:
    return ScanStats(
        unit_count=unit_count,
        unit_label=label,
        component_count=components,
        sources=set(sources),
    )


def test_text_no_findings_shows_count_summary():
    out = render_text([], {}, _stats(unit_count=2, components=86))
    assert "Scanned 2 manifests, 86 components" in out
    assert "no findings" in out


def test_text_no_findings_pluralizes_correctly():
    out = render_text([], {}, _stats(unit_count=1, components=1))
    # Singular form.
    assert "Scanned 1 manifest," in out
    assert "1 component" in out


def test_text_grouped_block_per_component():
    """One package, two findings → single group with two finding rows."""
    findings = [
        _finding("GHSA-A", "urllib3", "2.6.3"),
        _finding("GHSA-B", "urllib3", "2.6.3"),
    ]
    index = {
        "GHSA-A": _advisory(
            "GHSA-A",
            "npm",
            "urllib3",
            fixed="2.6.4",
            summary="CSRF via Proxy-Authorization header",
            severity_label="HIGH",
        ),
        "GHSA-B": _advisory(
            "GHSA-B",
            "npm",
            "urllib3",
            fixed="2.7.0",
            summary="Pool memory bypass",
            severity_label="HIGH",
        ),
    }
    out = render_text(findings, index, _stats(unit_count=1, components=1))
    assert "Found 2 vulnerabilities in 1 package." in out
    # Group header appears once.
    assert out.count("urllib3 2.6.3") == 1
    # Both findings present.
    assert "GHSA-A" in out
    assert "GHSA-B" in out
    # fix: line aggregates to the max fixed version.
    assert "upgrade to >=2.7.0" in out
    # Severity labels per row, both HIGH.
    assert out.count("HIGH") == 2
    # Source tag present.
    assert "[asve.dev]" in out


def test_text_groups_sorted_by_max_severity_desc():
    """Two packages, one HIGH one MEDIUM → HIGH group renders first."""
    findings = [
        _finding("MED", "low-prio", "1.0.0", manifest="low.json"),
        _finding("HI", "urgent", "2.0.0", manifest="urgent.json"),
    ]
    index = {
        "MED": _advisory("MED", "npm", "low-prio", severity_label="MEDIUM"),
        "HI": _advisory("HI", "npm", "urgent", severity_label="HIGH"),
    }
    out = render_text(findings, index, _stats(components=2))
    urgent_idx = out.index("urgent 2.0.0")
    lowprio_idx = out.index("low-prio 1.0.0")
    assert urgent_idx < lowprio_idx


def test_text_attributed_finding_shows_via_and_remove_fix():
    findings = [
        _finding(
            "ASVE-X",
            "@supabase/mcp-server",
            "1.0.4",
            attributed_to="claude-plugin/supabase@0.1.6",
            manifest="~/.claude/cache/supabase/0.1.6/.mcp.json",
        )
    ]
    index = {
        "ASVE-X": _advisory(
            "ASVE-X",
            "npm",
            "@supabase/mcp-server",
            fixed="1.0.5",
            summary="Token exposure via tool output",
            severity_label="CRITICAL",
        )
    }
    out = render_text(findings, index, _stats())
    assert "via:      claude-plugin/supabase@0.1.6" in out
    assert "fix:      upgrade or remove claude-plugin/supabase@0.1.6" in out
    assert "CRITICAL" in out


def test_text_fix_falls_back_to_see_findings_when_versions_unparseable():
    findings = [_finding("X", "weird", "0.0.1")]
    index = {"X": _advisory("X", "npm", "weird", fixed="not.a.version")}
    out = render_text(findings, index, _stats())
    assert "fix:      see findings" in out


def test_text_color_escapes_present_when_enabled():
    findings = [_finding("X", "pkg", "1.0.0")]
    index = {"X": _advisory("X", "npm", "pkg", severity_label="HIGH")}
    out_colored = render_text(findings, index, _stats(), use_color=True)
    out_plain = render_text(findings, index, _stats(), use_color=False)
    assert "\x1b[" in out_colored
    assert "\x1b[" not in out_plain


def test_text_verbose_adds_surfaces_and_impact():
    findings = [_finding("X", "pkg", "1.0.0")]
    advisory = _advisory("X", "npm", "pkg", severity_label="HIGH")
    advisory["database_specific"]["asve"]["surfaces"] = ["tool_invocation", "stdio"]
    advisory["database_specific"]["asve"]["agent_impact"] = {
        "credential_exfiltration": True,
        "code_execution": False,
    }
    index = {"X": advisory}
    out_v = render_text(findings, index, _stats(), verbose=True)
    out_p = render_text(findings, index, _stats(), verbose=False)
    assert "surfaces: tool_invocation, stdio" in out_v
    assert "agent_impact: credential_exfiltration" in out_v
    assert "confidence:" in out_v
    assert "surfaces:" not in out_p


def test_text_footer_lists_sources():
    findings = [_finding("X", "pkg", "1.0.0")]
    index = {"X": _advisory("X", "npm", "pkg", severity_label="HIGH", source="osv.dev")}
    out = render_text(findings, index, _stats(sources=("asve.dev", "osv.dev")))
    assert "Sources: asve.dev + osv.dev" in out


# ── render_github ────────────────────────────────────────────────────────────


def test_github_emits_one_line_per_finding():
    findings = [
        _finding("A", "p", "1", confidence="high"),
        _finding("B", "p", "2", confidence="low"),
    ]
    out = render_github(findings)
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("::error file=")
    assert lines[1].startswith("::warning file=")
    assert "title=A" in lines[0]
    assert "title=B" in lines[1]


def test_github_empty_findings_returns_empty_string():
    assert render_github([]) == ""


def test_github_attributed_finding_includes_via_in_message():
    findings = [_finding("A", "p", "1", attributed_to="claude-plugin/x@1")]
    out = render_github(findings)
    assert "(via claude-plugin/x@1)" in out


# ── render_json ──────────────────────────────────────────────────────────────


def test_json_empty_returns_findings_array_and_stats():
    out = render_json([], {}, _stats(unit_count=0, components=0))
    parsed = json.loads(out)
    assert parsed["findings"] == []
    assert parsed["stats"]["units"] == 0
    assert parsed["stats"]["components"] == 0
    assert parsed["stats"]["sources"] == ["asve.dev"]


def test_json_finding_contains_full_record():
    findings = [_finding("A", "urllib3", "2.6.3")]
    index = {
        "A": _advisory(
            "A",
            "npm",
            "urllib3",
            fixed="2.7.0",
            summary="CSRF",
            severity_label="HIGH",
            source="osv.dev",
        )
    }
    out = render_json(findings, index, _stats(sources=("osv.dev",)))
    parsed = json.loads(out)
    (entry,) = parsed["findings"]
    assert entry["id"] == "A"
    assert entry["severity"] == "HIGH"
    assert entry["package"] == {"ecosystem": "npm", "name": "urllib3", "version": "2.6.3"}
    assert entry["fixed_in"] == "2.7.0"
    assert entry["summary"] == "CSRF"
    assert entry["source"] == "osv.dev"
    assert entry["confidence"] == "high"


def test_json_score_populated_when_vector_present():
    findings = [_finding("A", "urllib3", "2.6.3")]
    advisory = _advisory(
        "A",
        "npm",
        "urllib3",
        severity_vector="CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H",
    )
    index = {"A": advisory}
    out = render_json(findings, index, _stats())
    parsed = json.loads(out)
    score = parsed["findings"][0]["score"]
    assert score is not None
    assert score == pytest.approx(7.2, abs=0.05)


def test_json_score_none_when_only_upstream_label():
    findings = [_finding("A", "urllib3", "2.6.3")]
    index = {"A": _advisory("A", "npm", "urllib3", severity_label="HIGH")}
    out = render_json(findings, index, _stats())
    parsed = json.loads(out)
    # Severity label still HIGH (from upstream), but no numeric score.
    assert parsed["findings"][0]["severity"] == "HIGH"
    assert parsed["findings"][0]["score"] is None
