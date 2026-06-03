"""Tests for the output renderers in `tools/render.py`.

Three renderers (text, github, json) consume the same (findings,
advisory_index, stats) inputs. These tests cover the shape contract
each renderer makes with its consumer.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from tools.component_ref import ComponentRef
from tools.matcher import Finding
from tools.render import (
    ScanStats,
    _aggregate_fix,
    _fixed_in_for_finding,
    _group_findings,
    render_github,
    render_inventory_tree,
    render_json,
    render_repo_inventory_tree,
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
        extra=kw.get("extra", {}),
    )


def _advisory(
    advisory_id: str,
    ecosystem: str,
    pkg: str,
    fixed: str | None = "9.9.9",
    summary: str = "test summary",
    severity_label: str | None = None,
    severity_vector: str | None = None,
    source: str = "openaca.dev",
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
        "database_specific": {"openaca": {"source": source}},
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


def _stats(unit_count=1, components=1, label="manifest", sources=("openaca.dev",)) -> ScanStats:
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


def test_text_no_findings_appends_aca_framing_footer():
    """Zero-findings output explains that general software deps are out of scope."""
    out = render_text([], {}, _stats())
    assert "agent composition" in out
    assert "general-purpose SCA scanner" in out


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
    assert "[openaca.dev]" in out


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
            "OpenACA-X",
            "@supabase/mcp-server",
            "1.0.4",
            attributed_to="claude-plugin/supabase@0.1.6",
            manifest="~/.claude/cache/supabase/0.1.6/.mcp.json",
        )
    ]
    index = {
        "OpenACA-X": _advisory(
            "OpenACA-X",
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


def test_text_verbose_adds_taxonomies_and_evidence_level():
    findings = [_finding("X", "pkg", "1.0.0")]
    advisory = _advisory("X", "npm", "pkg", severity_label="HIGH")
    advisory["database_specific"]["openaca"]["taxonomies"] = {
        "owasp_agentic_top10": ["asi02", "asi05"]
    }
    advisory["database_specific"]["openaca"]["evidence_level"] = "confirmed"
    index = {"X": advisory}
    out_v = render_text(findings, index, _stats(), verbose=True)
    out_p = render_text(findings, index, _stats(), verbose=False)
    assert "taxonomies: owasp_agentic_top10=asi02,asi05" in out_v
    assert "evidence_level: confirmed" in out_v
    assert "confidence:" in out_v
    assert "taxonomies:" not in out_p


def test_text_verbose_adds_direct_component_identity_details():
    ref = ComponentRef(
        ecosystem="npm",
        name="@modelcontextprotocol/server-filesystem",
        version="1.0.2",
        source_manifest=".mcp.json",
        extra={
            "component_type": "mcp_server",
            "runtime_hosts": ["claude-code"],
            "declared_by": {"kind": "manifest", "path": ".mcp.json"},
            "component_path": [{"type": "mcp_server", "name": "filesystem"}],
        },
    )
    finding = Finding("GHSA-X", ref, "high")
    advisory = _advisory("GHSA-X", "npm", "@modelcontextprotocol/server-filesystem")

    out = render_text([finding], {"GHSA-X": advisory}, _stats(), verbose=True)

    assert "Component: mcp_server filesystem" in out
    assert "Source: pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.2" in out
    assert "Active in: claude-code" in out
    assert "Declared by: .mcp.json" in out


def test_text_verbose_adds_plugin_bundled_component_path():
    ref = ComponentRef(
        ecosystem="npm",
        name="@modelcontextprotocol/server-filesystem",
        version="1.0.2",
        source_manifest=".claude/cache/acme/.mcp.json",
        attributed_to="claude-plugin/acme-devtools@1.0.0",
        extra={
            "component_type": "mcp_server",
            "runtime_hosts": ["claude-code"],
            "declared_by": {
                "kind": "plugin",
                "name": "acme-devtools",
                "path": ".claude/cache/acme/.claude-plugin/plugin.json",
            },
            "component_path": [
                {"type": "plugin", "name": "acme-devtools"},
                {"type": "mcp_server", "name": "filesystem"},
            ],
        },
    )
    finding = Finding("GHSA-X", ref, "high", attributed_to=ref.attributed_to)
    advisory = _advisory("GHSA-X", "npm", "@modelcontextprotocol/server-filesystem")

    out = render_text([finding], {"GHSA-X": advisory}, _stats(), verbose=True)

    assert 'Declared by: plugin "acme-devtools"' in out
    assert "Path: plugin acme-devtools -> mcp_server filesystem" in out


def test_text_footer_lists_sources():
    findings = [_finding("X", "pkg", "1.0.0")]
    index = {"X": _advisory("X", "npm", "pkg", severity_label="HIGH", source="osv.dev")}
    out = render_text(findings, index, _stats(sources=("openaca.dev", "osv.dev")))
    assert "Sources: openaca.dev + osv.dev" in out


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


def test_github_emits_posture_findings_as_notice_for_low_severity():
    out = render_github([], posture_findings=[_posture(severity="low")])
    lines = out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("::notice ")
    assert "openaca-posture-mutable-install-reference" in lines[0]


def test_github_posture_severity_mapping():
    low = _posture(rule_id="rule-low", severity="low")
    medium = _posture(rule_id="rule-medium", severity="medium")
    high = _posture(rule_id="rule-high", severity="high")
    out = render_github([], posture_findings=[low, medium, high])
    lines = out.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("::notice ")
    assert lines[1].startswith("::warning ")
    assert lines[2].startswith("::error ")


def test_github_posture_appended_after_vuln_findings():
    findings = [_finding("A", "p", "1", confidence="high")]
    out = render_github(findings, posture_findings=[_posture(severity="low")])
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("::error ")  # vuln finding
    assert lines[1].startswith("::notice ")  # posture finding


def test_github_posture_omitted_when_not_passed():
    out = render_github([])
    assert "posture" not in out


# ── render_json ──────────────────────────────────────────────────────────────


def test_json_empty_returns_findings_array_and_stats():
    out = render_json([], {}, _stats(unit_count=0, components=0))
    parsed = json.loads(out)
    assert parsed["findings"] == []
    assert parsed["stats"]["units"] == 0
    assert parsed["stats"]["components"] == 0
    assert parsed["stats"]["sources"] == ["openaca.dev"]


def test_json_finding_contains_full_record():
    ref = ComponentRef(
        ecosystem="npm",
        name="urllib3",
        version="2.6.3",
        source_manifest="mcp.json",
        extra={
            "component_type": "mcp_server",
            "runtime_hosts": ["claude-code"],
            "declared_by": {"kind": "manifest", "path": "mcp.json"},
        },
    )
    findings = [Finding("A", ref, "high")]
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
    assert "posture_findings" not in parsed
    assert entry["finding_type"] == "vulnerability"
    assert entry["id"] == "A"
    assert entry["severity"] == "HIGH"
    assert entry["component"]["type"] == "mcp_server"
    assert entry["component"]["source"] == {
        "ecosystem": "npm",
        "purl": "pkg:npm/urllib3@2.6.3",
        "name": "urllib3",
        "version": "2.6.3",
    }
    assert entry["active_in"] == ["claude-code"]
    assert entry["declared_by"] == {"kind": "manifest", "path": "mcp.json"}
    assert entry["fixed_in"] == "2.7.0"
    assert entry["summary"] == "CSRF"
    assert entry["source"] == "osv.dev"
    assert entry["confidence"] == "high"
    assert entry["matched_advisory"]["id"] == "A"


def test_json_plugin_bundled_finding_contains_component_path():
    ref = ComponentRef(
        ecosystem="npm",
        name="@modelcontextprotocol/server-filesystem",
        version="1.0.2",
        source_manifest=".claude/cache/acme/.mcp.json",
        attributed_to="claude-plugin/acme-devtools@1.0.0",
        extra={
            "component_type": "mcp_server",
            "runtime_hosts": ["claude-code"],
            "declared_by": {
                "kind": "plugin",
                "name": "acme-devtools",
                "path": ".claude/cache/acme/.claude-plugin/plugin.json",
            },
            "component_path": [
                {"type": "plugin", "name": "acme-devtools"},
                {"type": "mcp_server", "name": "filesystem"},
            ],
        },
    )
    finding = Finding("GHSA-X", ref, "high")
    advisory = _advisory("GHSA-X", "npm", "@modelcontextprotocol/server-filesystem")

    doc = json.loads(render_json([finding], {"GHSA-X": advisory}, _stats()))
    (entry,) = doc["findings"]

    assert entry["finding_type"] == "vulnerability"
    assert entry["component"]["type"] == "mcp_server"
    assert entry["declared_by"]["kind"] == "plugin"
    assert entry["component_path"] == [
        {"type": "plugin", "name": "acme-devtools"},
        {"type": "mcp_server", "name": "filesystem"},
    ]


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


# ── render_inventory_tree ────────────────────────────────────────────────────


def _plugin_ref(
    name: str,
    version: str,
    scope: str = "user",
    sha: str = "",
    marketplace: str | None = None,
) -> ComponentRef:
    identity_name = f"{marketplace}/{name}" if marketplace else name
    extra = {"component_type": "plugin", "scope": scope, "gitCommitSha": sha}
    if marketplace:
        extra["marketplace"] = marketplace
    return ComponentRef(
        name=name,
        version=version,
        component_identity=f"claude-plugin/{identity_name}",
        source_manifest="installed_plugins.json",
        source_locator=f"$.plugins.{name}",
        extra=extra,
    )


def _bundled(
    eco: str,
    name: str | None,
    version: str | None,
    attributed_to: str | None,
    **kw,
) -> ComponentRef:
    ident = kw.get("component_identity")
    component_type = {
        "skill": "skill",
        "claude-command": "command",
        "claude-hook": "hook",
        "claude-agent": "agent",
    }.get(eco)
    extra = dict(kw.get("extra", {}))
    if component_type:
        extra.setdefault("component_type", component_type)
    return ComponentRef(
        ecosystem=None if component_type else eco,
        name=name,
        version=version,
        component_identity=ident,
        source_manifest=kw.get("source_manifest", "fake"),
        attributed_to=attributed_to,
        extra=extra,
    )


def test_tree_header_counts_plugins_direct_total():
    refs = [
        _plugin_ref("a", "1.0.0"),
        _bundled("npm", "@x/mcp", "1.0.0", attributed_to="claude-plugin/a@1.0.0"),
        _bundled(
            "skill",
            "direct-skill",
            None,
            attributed_to=None,  # direct
            component_identity="skill/direct-skill",
        ),
    ]
    out = render_inventory_tree(refs, [], use_unicode=True)
    # Header: 1 plugin, 1 direct component, 2 total components (skill + bundled MCP).
    assert "1 active plugin, 1 direct component, 2 total components" in out


def test_tree_groups_bundled_components_by_category():
    refs = [
        _plugin_ref("supabase", "0.1.6"),
        _bundled(
            "skill",
            "supa-skill",
            None,
            attributed_to="claude-plugin/supabase@0.1.6",
            component_identity="skill/supa-skill",
        ),
        _bundled(
            "npm",
            "@supabase/mcp",
            "1.0.0",
            attributed_to="claude-plugin/supabase@0.1.6",
        ),
    ]
    out = render_inventory_tree(refs, [], use_unicode=True)
    assert "claude-plugin/supabase@0.1.6" in out
    # Both categories appear with counts.
    assert "MCPs/ (1)" in out
    assert "skills/ (1)" in out
    # MCPs render before skills (declared category order).
    assert out.index("MCPs/") < out.index("skills/")


def test_tree_remote_mcp_leaf_shows_url_and_transport():
    refs = [
        _plugin_ref("github", "unknown", marketplace="official"),
        ComponentRef(
            component_identity="mcp-remote/api.githubcopilot.com/mcp/",
            attributed_to="claude-plugin/official/github@unknown",
            extra={
                "component_type": "mcp_server",
                "url": "https://api.githubcopilot.com/mcp/",
                "transport": "http",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "https://api.githubcopilot.com/mcp/ (HTTP)" in out
    assert "mcp-remote/api.githubcopilot.com/mcp/" not in out


def test_tree_remote_mcp_leaf_redacts_url_credentials():
    refs = [
        ComponentRef(
            component_identity="mcp-remote/api.example.com/mcp",
            extra={
                "component_type": "mcp_server",
                "url": "https://user:secret@api.example.com/mcp",
                "transport": "http",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "user:secret" not in out
    assert "api.example.com/mcp (HTTP)" in out


def test_tree_remote_mcp_leaf_redacts_url_query_secrets():
    refs = [
        ComponentRef(
            component_identity="mcp-remote/api.example.com/mcp",
            extra={
                "component_type": "mcp_server",
                "url": "https://api.example.com/mcp?api_key=sk-secret123&token=abc",
                "transport": "http",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "api_key" not in out
    assert "sk-secret123" not in out
    assert "token=abc" not in out
    assert "api.example.com/mcp (HTTP)" in out


def test_tree_remote_mcp_leaf_redacts_malformed_port_url():
    refs = [
        ComponentRef(
            component_identity="mcp-remote/host/mcp",
            extra={
                "component_type": "mcp_server",
                "url": "https://user:secret@host:abc/mcp?token=sk-secret",
                "transport": "http",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "user:secret" not in out
    assert "token=sk-secret" not in out
    assert "https://host:abc/mcp (HTTP)" in out


def test_tree_stdio_mcp_leaf_uses_structured_package_identity():
    refs = [
        _plugin_ref("playwright", "unknown", marketplace="official"),
        ComponentRef(
            ecosystem="npm",
            name="@playwright/mcp",
            version="latest",
            attributed_to="claude-plugin/official/playwright@unknown",
            extra={
                "component_type": "mcp_server",
                "install_source": "npx @playwright/mcp@latest",
                "transport": "stdio",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "@playwright/mcp@latest (stdio via npx)" in out
    assert "npx @playwright/mcp@latest" not in out


def test_tree_stdio_mcp_leaf_uses_github_source_identity():
    refs = [
        _plugin_ref("serena", "unknown", marketplace="official"),
        ComponentRef(
            ecosystem="github",
            name="oraios/serena",
            attributed_to="claude-plugin/official/serena@unknown",
            extra={
                "component_type": "mcp_server",
                "install_source": (
                    "uvx --from git+https://github.com/oraios/serena serena start-mcp-server"
                ),
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "oraios/serena (stdio via uvx)" in out
    assert "git+https://github.com/oraios/serena" not in out


def test_tree_stdio_mcp_leaf_uses_docker_source_identity():
    refs = [
        _plugin_ref("terraform", "unknown", marketplace="official"),
        ComponentRef(
            ecosystem="docker",
            name="hashicorp/terraform-mcp-server",
            version="0.4.0",
            attributed_to="claude-plugin/official/terraform@unknown",
            extra={
                "component_type": "mcp_server",
                "install_source": "docker run -e TFE_TOKEN=${TFE_TOKEN} "
                "hashicorp/terraform-mcp-server:0.4.0",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "hashicorp/terraform-mcp-server@0.4.0 (stdio via docker)" in out
    assert "TFE_TOKEN" not in out


def test_tree_stdio_mcp_leaf_uses_bom_canonicalized_source_identity():
    refs = [
        _plugin_ref("sources", "unknown", marketplace="official"),
        ComponentRef(
            ecosystem="GitHub",
            name="oraios/serena",
            attributed_to="claude-plugin/official/sources@unknown",
            extra={
                "component_type": "mcp_server",
                "install_source": "uvx --from git+https://github.com/oraios/serena serena",
            },
        ),
        ComponentRef(
            ecosystem="Docker",
            name="hashicorp/terraform-mcp-server",
            version="0.4.0",
            attributed_to="claude-plugin/official/sources@unknown",
            extra={
                "component_type": "mcp_server",
                "install_source": "docker run hashicorp/terraform-mcp-server:0.4.0",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "oraios/serena (stdio via uvx)" in out
    assert "hashicorp/terraform-mcp-server@0.4.0 (stdio via docker)" in out
    assert "uvx (stdio, args hidden)" not in out
    assert "docker (stdio, args hidden)" not in out


def test_tree_local_stdio_mcp_leaf_uses_server_identity():
    refs = [
        ComponentRef(
            component_identity="mcp-stdio/local:discord",
            extra={
                "component_type": "mcp_server",
                "install_source": (
                    "bun run --cwd ${CLAUDE_PLUGIN_ROOT} --shell=bun --silent start"
                ),
                "component_path": [{"type": "mcp_server", "name": "discord"}],
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "discord (stdio via bun, local)" in out
    assert "${CLAUDE_PLUGIN_ROOT}" not in out


def test_tree_source_less_stdio_mcp_leaf_shows_command_only():
    refs = [
        ComponentRef(
            component_identity="mcp-stdio/binary:npx",
            extra={
                "component_type": "mcp_server",
                "install_source": "npx my-mcp-server --api-key=sk-secret123 --token secret",
            },
        )
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "sk-secret123" not in out
    assert "secret" not in out
    assert "my-mcp-server" not in out
    assert "npx (stdio, args hidden)" in out


def test_tree_source_less_unpinned_stdio_mcp_leaf_shows_safe_package_name():
    refs = [
        ComponentRef(
            component_identity="mcp-stdio/npx-unpinned:@modelcontextprotocol/server-filesystem",
            extra={
                "component_type": "mcp_server",
                "install_source": (
                    "npx @modelcontextprotocol/server-filesystem --api-key=sk-secret123"
                ),
            },
        ),
        ComponentRef(
            component_identity="mcp-stdio/uvx-unpinned:some-mcp-server",
            extra={
                "component_type": "mcp_server",
                "install_source": "uvx some-mcp-server --token secret",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "@modelcontextprotocol/server-filesystem (stdio via npx, unpinned)" in out
    assert "some-mcp-server (stdio via uvx, unpinned)" in out
    assert "sk-secret123" not in out
    assert "secret" not in out
    assert "args hidden" not in out


def test_tree_source_less_unpinned_stdio_mcp_url_spec_falls_back_to_args_hidden():
    """URL-style npx specs (e.g. with query-string tokens) must not be rendered verbatim."""
    refs = [
        ComponentRef(
            component_identity="mcp-stdio/npx-unpinned:https://registry.example.com/my-pkg?token=abc123",
            extra={
                "component_type": "mcp_server",
                "install_source": "npx https://registry.example.com/my-pkg?token=abc123",
            },
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "token=abc123" not in out
    assert "https://" not in out
    assert "npx (stdio, args hidden)" in out


def test_tree_plugin_name_parser_keeps_scoped_plugin_names_without_version():
    refs = [
        ComponentRef(
            name="@acme/tool",
            component_identity="claude-plugin/market/@acme/tool",
            source_manifest="installed_plugins.json",
            source_locator="$.plugins.@acme/tool@market",
            extra={"component_type": "plugin", "scope": "user", "marketplace": "market"},
        ),
        _bundled(
            "claude-command",
            "deploy",
            None,
            attributed_to="claude-plugin/market/@acme/tool",
            component_identity="claude-command/@acme/tool/deploy",
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "deploy" in out
    assert "@acme/tool/deploy" not in out


def test_tree_plugin_header_shows_marketplace_context():
    refs = [_plugin_ref("superpowers", "5.1.0", sha="917e5f53abcdef", marketplace="official")]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "claude-plugin/official/superpowers@5.1.0" in out
    assert "(sha: 917e5f53)" in out
    assert "[scope=user]" in out


def test_tree_empty_plugin_shows_no_bundled_components():
    refs = [_plugin_ref("github", "unknown")]
    out = render_inventory_tree(refs, [], use_unicode=True)
    assert "claude-plugin/github@unknown" in out
    assert "(no bundled components)" in out


def test_tree_direct_skill_renders_known_source_provenance():
    ref = ComponentRef(
        name="aws-api",
        component_identity="skill/aws-api",
        source_manifest="/Users/test/.claude/skills/aws-api/SKILL.md",
        extra={
            "component_type": "skill",
            "source_provenance": {
                "status": "known",
                "source_type": "github",
                "source": "awslabs/agent-toolkit-for-aws",
                "ref": "main",
                "skill_path": "skills/aws-api/SKILL.md",
                "hash": "1234567890abcdef",
            },
        },
    )

    out = render_inventory_tree([ref], [], use_unicode=True)

    assert (
        "aws-api (source: github:awslabs/agent-toolkit-for-aws#main, "
        "path: skills/aws-api/SKILL.md, hash: 12345678)"
    ) in out


def test_tree_direct_skill_renders_symlink_target_source_provenance():
    ref = ComponentRef(
        name="loose-skill",
        component_identity="skill/loose-skill",
        source_manifest="/Users/test/.claude/skills/loose-skill/SKILL.md",
        extra={
            "component_type": "skill",
            "source_provenance": {
                "status": "symlink-target",
                "resolved_path": "/Users/test/.agents/skills/loose-skill/SKILL.md",
            },
        },
    )

    out = render_inventory_tree([ref], [], use_unicode=True)

    assert "loose-skill (source: symlink -> /Users/test/.agents/skills/loose-skill/SKILL.md)" in out


def test_tree_renders_logical_identities_without_location_prefix():
    """Bundled commands render by logical command name; hooks render the
    observed command and trigger metadata instead of the identity hash."""
    refs = [
        _plugin_ref("supabase", "0.1.6"),
        _bundled(
            "claude-command",
            "deploy",
            None,
            attributed_to="claude-plugin/supabase@0.1.6",
            component_identity="claude-command/deploy",
        ),
        _bundled(
            "claude-hook",
            None,
            None,
            attributed_to="claude-plugin/supabase@0.1.6",
            component_identity="claude-hook/command:abcd1234",
            extra={"event": "PreToolUse", "index": 0, "command": "echo pre"},
        ),
    ]
    out = render_inventory_tree(refs, [], use_unicode=True)
    assert "deploy" in out
    assert "supabase/deploy" not in out
    assert "PreToolUse[0]: echo pre" in out
    assert "command:abcd1234" not in out


def test_tree_aggregates_tier2_deps_into_single_line():
    refs = [
        _plugin_ref("demo", "1.0.0"),
        _bundled(
            "npm",
            "lodash",
            "4.17.20",
            attributed_to="claude-plugin/demo@1.0.0",
            extra={"transitive": True},
        ),
        _bundled(
            "npm",
            "underscore",
            "1.13.0",
            attributed_to="claude-plugin/demo@1.0.0",
            extra={"transitive": True},
        ),
    ]
    out = render_inventory_tree(refs, [], use_unicode=True)
    # No individual lodash/underscore leaves — they aggregate.
    assert "lodash" not in out
    assert "underscore" not in out
    assert "npm/ deps (2 transitive via package-lock.json)" in out


def test_tree_tier2_direct_only_when_no_lockfile():
    refs = [
        _plugin_ref("demo", "1.0.0"),
        _bundled(
            "npm",
            "lodash",
            "4.17.20",
            attributed_to="claude-plugin/demo@1.0.0",
            extra={"transitive": False},
        ),
    ]
    out = render_inventory_tree(refs, [], use_unicode=True)
    assert "npm/ deps (1 direct only via package.json)" in out


def test_tree_tier2_aggregate_carries_finding_marker():
    """A finding on a transitive dep must surface as [! ...] on the aggregate line."""
    tier2_ref = _bundled(
        "npm",
        "lodash",
        "4.17.20",
        attributed_to="claude-plugin/demo@1.0.0",
        extra={"transitive": True},
        source_manifest="package-lock.json",
    )
    refs = [_plugin_ref("demo", "1.0.0"), tier2_ref]
    finding = Finding(
        advisory_id="CVE-2026-0001",
        component=tier2_ref,
        confidence="high",
        reason="lodash@4.17.20 matches CVE-2026-0001",
        attributed_to="claude-plugin/demo@1.0.0",
    )
    out = render_inventory_tree(refs, [finding], use_unicode=True, use_color=False)
    assert "[! CVE-2026-0001]" in out
    assert "npm/ deps" in out


def test_tree_direct_components_render_as_separate_root():
    refs = [
        _bundled(
            "skill",
            "foo",
            None,
            attributed_to=None,
            component_identity="skill/foo",
        ),
        _bundled(
            "skill",
            "bar",
            None,
            attributed_to=None,
            component_identity="skill/bar",
        ),
    ]
    out = render_inventory_tree(refs, [], use_unicode=True)
    assert "direct components/" in out
    assert "foo" in out
    assert "from fake" not in out
    assert "skills/ (2)" in out
    # Alphabetical: bar before foo.
    assert out.index("bar") < out.index("foo")


def test_tree_disambiguates_duplicate_direct_component_labels_with_source():
    refs = [
        _bundled(
            "skill",
            "bootstrap",
            "1.0.0",
            attributed_to=None,
            component_identity="skill/bootstrap@1.0.0",
            source_manifest="project/.claude/skills/bootstrap/SKILL.md",
        ),
        _bundled(
            "skill",
            "bootstrap",
            "1.0.0",
            attributed_to=None,
            component_identity="skill/bootstrap@1.0.0",
            source_manifest="project/.worktrees/feature/.claude/skills/bootstrap/SKILL.md",
        ),
    ]

    out = render_inventory_tree(refs, [], use_unicode=True)

    assert "bootstrap@1.0.0 (from project/.claude/skills/bootstrap/SKILL.md)" in out
    assert (
        "bootstrap@1.0.0 (from project/.worktrees/feature/.claude/skills/bootstrap/SKILL.md)" in out
    )


def test_tree_marks_affected_leaves_with_finding_id():
    """When a finding fires against a component, the leaf gets a `[! <id>]`
    suffix so users can locate vulnerabilities inside the inventory tree."""
    plugin = _plugin_ref("playwright", "1.0.0")
    ref = _bundled(
        "npm",
        "@playwright/mcp",
        "latest",
        attributed_to="claude-plugin/playwright@1.0.0",
    )
    finding = Finding(
        advisory_id="GHSA-X",
        component=ref,
        confidence="high",
        reason="match",
        attributed_to="claude-plugin/playwright@1.0.0",
    )
    out = render_inventory_tree([plugin, ref], [finding], use_unicode=True)
    assert "[! GHSA-X]" in out
    # The marker sits adjacent to the affected leaf, not on the plugin header.
    assert "@playwright/mcp@latest  [! GHSA-X]" in out


def test_tree_marks_plugin_header_when_plugin_advisory_matches():
    plugin = _plugin_ref("supabase", "0.1.0")
    finding = Finding(
        advisory_id="CVE-2026-XXXX",
        component=plugin,
        confidence="high",
        reason="match",
    )
    out = render_inventory_tree([plugin], [finding], use_unicode=True)
    # Header line carries the marker; the empty-plugin "(no bundled)" line
    # is separate.
    plugin_line = [line for line in out.splitlines() if "claude-plugin/supabase@0.1.0" in line][0]
    assert "[! CVE-2026-XXXX]" in plugin_line


def test_tree_ascii_fallback_uses_ascii_chars():
    refs = [
        _plugin_ref("a", "1.0.0"),
        _bundled(
            "skill",
            "x",
            None,
            attributed_to="claude-plugin/a@1.0.0",
            component_identity="skill/x",
        ),
    ]
    out = render_inventory_tree(refs, [], use_unicode=False)
    # ASCII connector chars present; Unicode absent.
    assert "├──" not in out
    assert "└──" not in out
    assert "`-- " in out or "|-- " in out


def test_tree_unicode_default():
    refs = [
        _plugin_ref("a", "1.0.0"),
        _bundled(
            "skill",
            "x",
            None,
            attributed_to="claude-plugin/a@1.0.0",
            component_identity="skill/x",
        ),
    ]
    out = render_inventory_tree(refs, [], use_unicode=True)
    # Unicode connectors present.
    assert ("├──" in out) or ("└──" in out)


def test_tree_plugins_sorted_alphabetically():
    refs = [
        _plugin_ref("zeta", "1.0.0"),
        _plugin_ref("alpha", "1.0.0"),
        _plugin_ref("mu", "1.0.0"),
    ]
    out = render_inventory_tree(refs, [], use_unicode=True)
    alpha_idx = out.index("claude-plugin/alpha")
    mu_idx = out.index("claude-plugin/mu")
    zeta_idx = out.index("claude-plugin/zeta")
    assert alpha_idx < mu_idx < zeta_idx


def test_tree_finding_marker_color_when_enabled():
    plugin = _plugin_ref("a", "1.0.0")
    ref = _bundled("npm", "x", "1.0.0", attributed_to="claude-plugin/a@1.0.0")
    finding = Finding(advisory_id="X", component=ref, confidence="high")
    colored = render_inventory_tree([plugin, ref], [finding], use_color=True, use_unicode=True)
    plain = render_inventory_tree([plugin, ref], [finding], use_color=False, use_unicode=True)
    assert "\x1b[31m" in colored
    assert "\x1b[" not in plain


# ── render_repo_inventory_tree ───────────────────────────────────────────────


def test_repo_tree_groups_plugin_root_deps_and_mcp_under_plugin(tmp_path):
    plugin_json = tmp_path / ".claude-plugin" / "plugin.json"
    plugin_json.parent.mkdir()
    package_json = tmp_path / "package.json"
    mcp_json = tmp_path / ".mcp.json"
    plugin = ComponentRef(
        name="demo-plugin",
        version="1.0.0",
        component_identity="claude-plugin/demo-plugin",
        source_manifest=str(plugin_json),
        extra={"component_type": "plugin"},
    )
    dep = ComponentRef(
        ecosystem="npm",
        name="lodash",
        version="4.17.20",
        source_manifest=str(package_json),
        scope="agent-dependency",
    )
    mcp = ComponentRef(
        ecosystem="npm",
        name="@cyanheads/git-mcp-server",
        version="1.1.0",
        source_manifest=str(mcp_json),
        scope="agent-component",
    )
    finding = Finding(advisory_id="GHSA-L", component=dep, confidence="high")

    out = render_repo_inventory_tree(
        tmp_path,
        [(package_json, [dep]), (mcp_json, [mcp]), (plugin_json, [plugin])],
        [finding],
        use_unicode=True,
    )

    assert f"repo {tmp_path}" in out
    assert "claude-plugin/demo-plugin@1.0.0" in out
    assert "package deps/ (1)" in out
    assert "lodash@4.17.20 (from package.json)  [! GHSA-L]" in out
    assert "MCPs/ (1)" in out
    assert "@cyanheads/git-mcp-server@1.1.0 (from .mcp.json)" in out


def test_repo_tree_shows_direct_components_and_suppressed_software(tmp_path):
    package_json = tmp_path / "package.json"
    mcp_json = tmp_path / ".mcp.json"
    software_dep = ComponentRef(
        ecosystem="npm",
        name="left-pad",
        version="1.0.0",
        source_manifest=str(package_json),
        scope="software-dependency",
    )
    mcp = ComponentRef(
        ecosystem="npm",
        name="@example/mcp",
        version="2.0.0",
        source_manifest=str(mcp_json),
        scope="agent-component",
    )

    out = render_repo_inventory_tree(
        tmp_path,
        [(package_json, [software_dep]), (mcp_json, [mcp])],
        [],
        use_unicode=True,
    )

    assert "direct components/" in out
    assert "MCPs/ (1)" in out
    assert f"@example/mcp@2.0.0 (from {mcp_json.relative_to(tmp_path)})" in out
    assert "software deps suppressed/ (1)" in out
    assert "left-pad" not in out


def test_repo_tree_attributed_refs_scoped_to_plugin_dir(tmp_path):
    """Two plugins with the same name and version must not cross-contaminate.

    Both emit attributed_to = "claude-plugin/foo@1.0", so matching solely on
    attributed_to would assign skill-A under plugin-B and vice-versa.  The fix
    scopes the attributed_to match to the ref's plugin root directory.
    """
    plugin_a_root = tmp_path / "plugin-a"
    plugin_b_root = tmp_path / "plugin-b"
    for root in (plugin_a_root, plugin_b_root):
        (root / ".claude-plugin").mkdir(parents=True)

    plugin_a_json = plugin_a_root / ".claude-plugin" / "plugin.json"
    plugin_b_json = plugin_b_root / ".claude-plugin" / "plugin.json"

    plugin_a = ComponentRef(
        name="foo",
        version="1.0",
        component_identity="claude-plugin/foo",
        source_manifest=str(plugin_a_json),
        extra={"component_type": "plugin"},
    )
    plugin_b = ComponentRef(
        name="foo",
        version="1.0",
        component_identity="claude-plugin/foo",
        source_manifest=str(plugin_b_json),
        extra={"component_type": "plugin"},
    )

    skill_a_md = plugin_a_root / "skills" / "skill-a" / "SKILL.md"
    skill_b_md = plugin_b_root / "skills" / "skill-b" / "SKILL.md"
    for md in (skill_a_md, skill_b_md):
        md.parent.mkdir(parents=True)
        md.write_text("---\nname: {}\ndescription: test\n---\nbody\n".format(md.parent.name))

    # Both skill refs carry the same attributed_to string.
    skill_a = ComponentRef(
        component_identity="skill/skill-a",
        source_manifest=str(skill_a_md),
        scope="agent-component",
        attributed_to="claude-plugin/foo@1.0",
        extra={"component_type": "skill"},
    )
    skill_b = ComponentRef(
        component_identity="skill/skill-b",
        source_manifest=str(skill_b_md),
        scope="agent-component",
        attributed_to="claude-plugin/foo@1.0",
        extra={"component_type": "skill"},
    )

    out = render_repo_inventory_tree(
        tmp_path,
        [
            (plugin_a_json, [plugin_a, skill_a]),
            (plugin_b_json, [plugin_b, skill_b]),
        ],
        [],
        use_unicode=True,
    )

    # skill-a must appear exactly once as a leaf; skill-b likewise. Their
    # source paths also contain the skill names.
    assert out.count("── skill-a ") == 1
    assert out.count("── skill-b ") == 1
    # Each plugin node must show exactly 1 skill, not 2.
    assert out.count("skills/ (2)") == 0


def test_repo_tree_nested_plugin_attributed_refs_not_claimed_by_ancestor(tmp_path):
    """Plugin B nested inside plugin A's directory must not have its skills
    claimed by plugin A's tree node, even when both share the same name/version.

    _ref_owned_by_plugin must detect that inner plugin dir is a closer ancestor
    of inner skill refs and exclude them from the outer plugin's node.
    """
    outer_root = tmp_path / "outer"
    inner_root = outer_root / "nested" / "inner"
    (outer_root / ".claude-plugin").mkdir(parents=True)
    (inner_root / ".claude-plugin").mkdir(parents=True)

    outer_json = outer_root / ".claude-plugin" / "plugin.json"
    inner_json = inner_root / ".claude-plugin" / "plugin.json"

    outer_plugin = ComponentRef(
        name="foo",
        version="1.0",
        component_identity="claude-plugin/foo",
        source_manifest=str(outer_json),
        extra={"component_type": "plugin"},
    )
    inner_plugin = ComponentRef(
        name="foo",
        version="1.0",
        component_identity="claude-plugin/foo",
        source_manifest=str(inner_json),
        extra={"component_type": "plugin"},
    )

    outer_skill_md = outer_root / "skills" / "outer-skill" / "SKILL.md"
    inner_skill_md = inner_root / "skills" / "inner-skill" / "SKILL.md"
    for md in (outer_skill_md, inner_skill_md):
        md.parent.mkdir(parents=True)
        md.write_text("---\nname: {}\ndescription: test\n---\nbody\n".format(md.parent.name))

    outer_skill = ComponentRef(
        component_identity="skill/outer-skill",
        source_manifest=str(outer_skill_md),
        scope="agent-component",
        attributed_to="claude-plugin/foo@1.0",
        extra={"component_type": "skill"},
    )
    inner_skill = ComponentRef(
        component_identity="skill/inner-skill",
        source_manifest=str(inner_skill_md),
        scope="agent-component",
        attributed_to="claude-plugin/foo@1.0",
        extra={"component_type": "skill"},
    )

    out = render_repo_inventory_tree(
        tmp_path,
        [
            (outer_json, [outer_plugin, outer_skill]),
            (inner_json, [inner_plugin, inner_skill]),
        ],
        [],
        use_unicode=True,
    )

    # Each skill must appear exactly once as a leaf; source paths also contain
    # the skill names.
    assert out.count("── outer-skill ") == 1
    assert out.count("── inner-skill ") == 1
    assert out.count("skills/ (2)") == 0


# ── Posture findings section ─────────────────────────────────────────────────


def _posture(rule_id="openaca-posture-mutable-install-reference", severity: str = "low"):
    from tools.posture import PostureFinding, Standards

    return PostureFinding(
        rule_id=rule_id,
        title="Component installed from a mutable source reference",
        severity=severity,  # type: ignore[arg-type]
        confidence="high",
        component={"type": "mcp_server", "name": "npm/foo (npx foo)"},
        active_in=["claude-code"],
        declared_by={"kind": "manifest", "path": ".mcp.json"},
        component_path=[{"type": "mcp_server", "name": "npm/foo (npx foo)"}],
        standards=Standards(cwe=["CWE-1357"], owasp_agentic_top10=["asi04"]),
        remediation="Pin to an exact version, commit SHA, or Docker digest.",
    )


def test_text_renders_posture_section_when_no_vuln_findings():
    out = render_text(
        [],
        {},
        _stats(unit_count=1, components=4),
        posture_findings=[_posture()],
    )
    assert "Posture findings (configuration hygiene)" in out
    assert "LOW" in out
    assert "openaca-posture-mutable-install-reference" in out
    assert "CWE-1357" in out
    assert "asi04" in out


def test_text_renders_posture_section_alongside_vuln_findings():
    findings = [_finding("GHSA-A", "urllib3", "2.6.3")]
    index = {"GHSA-A": _advisory("GHSA-A", "npm", "urllib3", fixed="2.6.4")}
    out = render_text(
        findings,
        index,
        _stats(),
        posture_findings=[_posture()],
    )
    assert "Posture findings" in out
    # Vuln content still rendered.
    assert "urllib3" in out


def test_text_posture_omitted_when_kwarg_not_passed():
    out = render_text([], {}, _stats(unit_count=1, components=1))
    assert "Posture findings" not in out


def test_json_includes_posture_findings_in_unified_findings_array():
    out = render_json(
        [],
        {},
        _stats(unit_count=0, components=0),
        posture_findings=[_posture()],
    )
    doc = json.loads(out)
    assert "posture_findings" not in doc
    assert len(doc["findings"]) == 1
    pf = doc["findings"][0]
    assert pf["finding_type"] == "posture"
    assert pf["rule_id"] == "openaca-posture-mutable-install-reference"
    assert pf["severity"] == "low"
    assert pf["component"]["type"] == "mcp_server"
    assert pf["declared_by"] == {"kind": "manifest", "path": ".mcp.json"}
    assert pf["standards"]["cwe"] == ["CWE-1357"]
    assert pf["standards"]["owasp_agentic_top10"] == ["asi04"]


def test_json_omits_posture_findings_key_when_not_passed():
    out = render_json([], {}, _stats(unit_count=0, components=0))
    doc = json.loads(out)
    assert "posture_findings" not in doc


# ── Golden snapshots ─────────────────────────────────────────────────────────
#
# These pin the *exact* default text output so the first-run-card refactor
# (plan 022) can prove it does not regress the legacy (no-card-args) body and,
# later, lock the new card shape. Snapshots are ANSI-stripped and path-stable.
# Regenerate intentionally with: OPENACA_REGEN_GOLDEN=1 uv run pytest tests/test_render.py -k golden

_REPORTS_DIR = Path(__file__).parent / "fixtures" / "reports"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _assert_golden(actual: str, name: str) -> None:
    normalized = _strip_ansi(actual).rstrip("\n") + "\n"
    path = _REPORTS_DIR / name
    if os.environ.get("OPENACA_REGEN_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized, encoding="utf-8")
        return
    assert path.exists(), f"missing golden fixture {path}; regen with OPENACA_REGEN_GOLDEN=1"
    expected = path.read_text(encoding="utf-8")
    assert normalized == expected, (
        f"output drifted from {name}:\n--- expected ---\n{expected}\n--- actual ---\n{normalized}"
    )


def _golden_findings() -> tuple[list, dict]:
    findings = [
        _finding("GHSA-3q26-f695-pp76", "@cyanheads/git-mcp-server", "1.1.0", manifest="mcp.json")
    ]
    index = {
        "GHSA-3q26-f695-pp76": _advisory(
            "GHSA-3q26-f695-pp76",
            "npm",
            "@cyanheads/git-mcp-server",
            fixed="2.1.5",
            summary="@cyanheads/git-mcp-server vulnerable to command injection in several tools",
            source="osv.dev",
        )
    }
    return findings, index


def test_golden_legacy_clean():
    """Legacy (no card args) clean-scan output — baseline before plan 022."""
    out = render_text([], {}, _stats(unit_count=2, components=86))
    _assert_golden(out, "legacy-clean.txt")


def test_golden_legacy_findings():
    """Legacy (no card args) findings output — baseline before plan 022."""
    findings, index = _golden_findings()
    out = render_text(findings, index, _stats(unit_count=1, components=1, sources=("osv.dev",)))
    _assert_golden(out, "legacy-findings.txt")


def test_golden_legacy_findings_posture():
    """Legacy (no card args) findings + posture output — baseline before plan 022."""
    findings, index = _golden_findings()
    out = render_text(
        findings,
        index,
        _stats(unit_count=1, components=1, sources=("osv.dev",)),
        posture_findings=[_posture()],
    )
    _assert_golden(out, "legacy-findings-posture.txt")


# ── Card mode (plan 022) ─────────────────────────────────────────────────────


def test_no_card_args_preserves_legacy_body():
    """All card args absent → legacy body, no Inventory/Summary section headers."""
    out = render_text([], {}, _stats(unit_count=2, components=86))
    assert "Inventory" not in out
    assert "Summary" not in out
    assert out.startswith("Scanned 2 manifests, 86 components — no findings.")


def test_card_mode_triggered_by_any_single_card_arg():
    """Any one of target/inventory_tree/next_actions switches to the card."""
    out = render_text(
        [],
        {},
        _stats(unit_count=1, components=1),
        inventory_tree="superpowers@5.1.0\n└── (no bundled components)",
    )
    assert "Inventory" in out
    assert "Summary" in out


def test_golden_card_endpoint():
    """Full endpoint card with findings (plan 022 shape)."""
    from tools.render import RenderTarget

    findings, index = _golden_findings()
    target = RenderTarget(
        host_surface="Claude Code",
        rows=[("config", "~/.claude"), ("project", "not included")],
    )
    tree = (
        "superpowers@5.1.0\n"
        "├── skills/ (1)\n"
        "│   └── brainstorming\n"
        "└── MCPs/ (1)\n"
        "    └── @cyanheads/git-mcp-server 1.1.0  [! GHSA-3q26-f695-pp76]"
    )
    out = render_text(
        findings,
        index,
        _stats(unit_count=1, components=2, label="active plugin", sources=("osv.dev",)),
        target=target,
        inventory_tree=tree,
        next_actions=[
            "include project-local config: openaca scan endpoint --project .",
            "emit Agent BOM: openaca bom endpoint --output openaca-bom.json",
        ],
    )
    _assert_golden(out, "card-endpoint.txt")


def test_golden_card_clean_scan():
    """Clean scan still shows Target + Inventory + Summary (the 'feels useful' win)."""
    from tools.render import RenderTarget

    target = RenderTarget(
        host_surface="Claude Code",
        rows=[("config", "~/.claude"), ("project", "not included")],
    )
    tree = "superpowers@5.1.0\n└── skills/ (1)\n    └── brainstorming"
    out = render_text(
        [],
        {},
        _stats(unit_count=1, components=1, label="active plugin", sources=("osv.dev",)),
        target=target,
        inventory_tree=tree,
        next_actions=["include project-local config: openaca scan endpoint --project ."],
    )
    _assert_golden(out, "card-clean.txt")


# ── Risk Attribution: containment-aware finding markers (plan 023) ───────────


def test_containment_marker_shape_and_distinct_from_direct():
    from tools.render import _containment_marker, _finding_marker

    assert _finding_marker(["GHSA-A"], use_color=False) == "  [! GHSA-A]"
    assert _containment_marker(["GHSA-A"], use_color=False) == "  [! bundles: GHSA-A]"
    assert _containment_marker([], use_color=False) == ""
    # Sorted + deduped.
    assert (
        _containment_marker(["GHSA-B", "GHSA-A", "GHSA-A"], use_color=False)
        == "  [! bundles: GHSA-A, GHSA-B]"
    )
    # Colored wraps in red.
    assert _containment_marker(["GHSA-A"], use_color=True).startswith("\x1b[31m")


def _f(advisory_id: str, component: ComponentRef) -> Finding:
    return Finding(
        advisory_id=advisory_id,
        component=component,
        confidence="high",
        reason=f"{advisory_id} match",
        attributed_to=component.attributed_to,
    )


def test_endpoint_tree_flags_plugin_for_bundled_finding():
    """A clean plugin that bundles a vulnerable MCP gets `[! bundles: …]` on its
    header; the bundled leaf keeps its own direct `[! …]`."""
    plugin = _plugin_ref("a", "1.0.0")
    mcp = _bundled("npm", "@x/mcp", "1.0.0", attributed_to="claude-plugin/a@1.0.0")
    out = render_inventory_tree([plugin, mcp], [_f("GHSA-CHILD", mcp)], use_unicode=True)
    header = [ln for ln in out.splitlines() if "claude-plugin/a@1.0.0" in ln][0]
    assert "[! bundles: GHSA-CHILD]" in header
    leaf = [ln for ln in out.splitlines() if "@x/mcp" in ln][0]
    assert "[! GHSA-CHILD]" in leaf


def test_endpoint_tree_direct_plugin_hit_is_not_bundles():
    """A finding on the plugin itself uses the direct marker, not `bundles:`."""
    plugin = _plugin_ref("a", "1.0.0")
    out = render_inventory_tree([plugin], [_f("GHSA-PLUGIN", plugin)], use_unicode=True)
    assert "[! GHSA-PLUGIN]" in out
    assert "bundles:" not in out


def test_endpoint_tree_direct_and_bundled_both_shown_no_dup():
    """Direct hit + vulnerable bundled child → both markers; the direct id is
    not duplicated into the bundles list."""
    plugin = _plugin_ref("a", "1.0.0")
    mcp = _bundled("npm", "@x/mcp", "1.0.0", attributed_to="claude-plugin/a@1.0.0")
    out = render_inventory_tree(
        [plugin, mcp], [_f("GHSA-PLUGIN", plugin), _f("GHSA-CHILD", mcp)], use_unicode=True
    )
    header = [ln for ln in out.splitlines() if "claude-plugin/a@1.0.0" in ln][0]
    assert "[! GHSA-PLUGIN]" in header
    assert "[! bundles: GHSA-CHILD]" in header
    assert "bundles: GHSA-PLUGIN" not in header


def test_findings_section_shows_introduction_path_by_default():
    """The containment path (how the component entered the stack) renders in the
    default Findings output, not just verbose."""
    ref = ComponentRef(
        ecosystem="npm",
        name="@modelcontextprotocol/server-filesystem",
        version="1.0.2",
        source_manifest=".mcp.json",
        attributed_to="claude-plugin/acme-devtools@1.0.0",
        extra={
            "component_type": "mcp_server",
            "component_path": [
                {"type": "plugin", "name": "acme-devtools"},
                {"type": "mcp_server", "name": "filesystem"},
            ],
        },
    )
    finding = Finding("GHSA-X", ref, "high", attributed_to=ref.attributed_to)
    advisory = _advisory("GHSA-X", "npm", "@modelcontextprotocol/server-filesystem")
    out = render_text([finding], {"GHSA-X": advisory}, _stats())
    assert "path:     plugin acme-devtools -> mcp_server filesystem" in out


def test_findings_section_omits_path_for_direct_component():
    """A direct component (no parent) has no introduction path."""
    findings = [_finding("GHSA-A", "urllib3", "2.6.3")]
    index = {"GHSA-A": _advisory("GHSA-A", "npm", "urllib3")}
    out = render_text(findings, index, _stats())
    assert "path:" not in out


def test_package_leaf_label_includes_github_subdirectory():
    """GitHub monorepo subpackages must render distinctly — the subdirectory is
    the only thing separating two MCPs at the same repo+revision."""
    from tools.render import _package_leaf_label

    sha = "0123456789abcdef0123456789abcdef01234567"
    ref = ComponentRef(
        ecosystem="github",
        name="org/mono",
        version=sha,
        extra={"source_subdirectory": "packages/mcp"},
    )
    assert _package_leaf_label(ref) == f"org/mono@{sha}#packages/mcp"


def test_package_leaf_label_without_subdirectory_unchanged():
    from tools.render import _package_leaf_label

    ref = ComponentRef(ecosystem="npm", name="@x/mcp", version="1.0.0")
    assert _package_leaf_label(ref) == "@x/mcp@1.0.0"
