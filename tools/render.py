"""Renderers for `asve-scan` output: text (default), github, json.

The CLI dispatches to one of three renderers based on `--format`:

- `render_text` — grouped by package, severity per finding, ANSI-colored
  when stdout is a TTY. Default for terminal use.
- `render_github` — GitHub workflow annotation lines (`::error file=...::`).
  Auto-selected when `GITHUB_ACTIONS=true`; CI parses these into PR-side
  annotations.
- `render_json` — structured records keyed by finding; for tool consumption.

Severity labels come from `tools.severity.derive_severity_label` (upstream
GHSA label wins; otherwise compute from CVSS vector; otherwise UNKNOWN).

The grouping shape (one block per `(ecosystem, name, version, manifest)`
tuple, severity per finding) is the format the user converged on. A package
with two HIGH advisories shows as one group with two rows, not two
near-identical blocks.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from packaging.version import InvalidVersion, Version

from tools.component_ref import ComponentRef
from tools.matcher import Finding
from tools.severity import derive_severity_label, derive_severity_score


@dataclass
class ScanStats:
    """Scan-level totals consumed by every renderer for the footer block.

    `unit_label` distinguishes repo-mode (`"manifest"`) from endpoint-mode
    (`"active plugin"`) so the human-facing footer reads accurately for each
    scan type. Pluralization is applied at render time.
    """

    unit_count: int = 0
    unit_label: str = "manifest"
    component_count: int = 0
    parse_failed: int = 0
    sources: set[str] = field(default_factory=set)


_SEVERITY_RANK = {
    "CRITICAL": 5,
    "HIGH": 4,
    "MEDIUM": 3,
    "LOW": 2,
    "NONE": 1,
    "UNKNOWN": 0,
}

_SEVERITY_COLOR = {
    "CRITICAL": "\x1b[1;31m",  # bold red
    "HIGH": "\x1b[31m",  # red
    "MEDIUM": "\x1b[33m",  # yellow
    "LOW": "\x1b[34m",  # blue
    "NONE": "\x1b[2m",  # dim
    "UNKNOWN": "\x1b[2m",
}
_RESET = "\x1b[0m"


# ── Helpers shared across formats ────────────────────────────────────────────


def _severity_rank(label: str) -> int:
    return _SEVERITY_RANK.get(label, 0)


def _label_for(finding: Finding, advisory_index: dict[str, dict]) -> str:
    return derive_severity_label(advisory_index.get(finding.advisory_id) or {})


def _color(label: str, use_color: bool) -> str:
    if not use_color:
        return label
    code = _SEVERITY_COLOR.get(label, "")
    if not code:
        return label
    return f"{code}{label}{_RESET}"


def _fixed_in_for_finding(finding: Finding, advisory: dict) -> Optional[str]:
    """Return the `fixed` version for the window that contains the component version.

    For advisories with multiple introduced/fixed windows (e.g. backported
    patches), returns the fix closing the *matched* window rather than the
    first fixed event, which may belong to a prior patch series the component
    has already surpassed.

    Falls back to the first fixed event when the component version is not
    parseable (low/unknown confidence findings cannot be range-narrowed).
    """
    eco = finding.component.ecosystem
    name = finding.component.name
    try:
        version: Optional[Version] = Version(finding.component.version or "")
    except (InvalidVersion, TypeError):
        version = None

    for entry in advisory.get("affected") or []:
        if not isinstance(entry, dict):
            continue
        pkg = entry.get("package") or {}
        if pkg.get("ecosystem") != eco or pkg.get("name") != name:
            continue
        for r in entry.get("ranges") or []:
            intro: Optional[str] = None
            for ev in r.get("events") or []:
                if not isinstance(ev, dict):
                    continue
                if "introduced" in ev:
                    intro = ev["introduced"]
                elif "fixed" in ev and intro is not None:
                    if version is None:
                        # Unparseable version — can't narrow to a window; return first fixed.
                        return str(ev["fixed"])
                    try:
                        intro_v = Version("0") if intro == "0" else Version(intro)
                        fixed_v = Version(ev["fixed"])
                    except InvalidVersion:
                        intro = None
                        continue
                    if version >= intro_v and version < fixed_v:
                        return str(ev["fixed"])
                    intro = None
                else:
                    # last_affected or limit — close window, no parseable fix target.
                    intro = None
    return None


def _summary_for_advisory(advisory: dict) -> str:
    """Single-line summary text for the right column of a finding row."""
    text = advisory.get("summary") or advisory.get("details") or advisory.get("id") or ""
    # Collapse newlines so a multi-line `details` block doesn't break the
    # one-row-per-finding format. Truncation is deliberately not applied —
    # terminals handle long lines fine; truncation only loses information.
    return " ".join(text.split())


def _source_for_advisory(advisory: dict) -> Optional[str]:
    ds = advisory.get("database_specific") or {}
    if not isinstance(ds, dict):
        return None
    asve = ds.get("asve") or {}
    if not isinstance(asve, dict):
        return None
    src = asve.get("source")
    return src if isinstance(src, str) else None


def _component_label(ref: ComponentRef) -> tuple[str, str]:
    """Return (name_part, version_part) for the group header. Empty strings
    when a field is missing — caller decides spacing."""
    if ref.name and ref.version:
        return ref.name, ref.version
    if ref.name:
        return ref.name, ""
    if ref.component_identity:
        return ref.component_identity, ""
    return "<unidentified>", ""


# ── Grouping ─────────────────────────────────────────────────────────────────


GroupKey = tuple[str, str, str, str]


def _group_key(finding: Finding) -> GroupKey:
    c = finding.component
    return (
        c.ecosystem or "",
        c.name or c.component_identity or "",
        c.version or "",
        str(c.source_manifest or ""),
    )


def _group_findings(findings: list[Finding]) -> "OrderedDict[GroupKey, list[Finding]]":
    """Group findings by (ecosystem, name, version, manifest). Insertion-order
    preserved; the caller re-sorts for display."""
    groups: OrderedDict[GroupKey, list[Finding]] = OrderedDict()
    for f in findings:
        groups.setdefault(_group_key(f), []).append(f)
    return groups


def _aggregate_fix(
    findings_in_group: list[Finding], advisory_index: dict[str, dict]
) -> Optional[str]:
    """The version a user must reach to clear ALL advisories in the group.

    Returns the maximum `fixed` version across every advisory in the group
    using semver-aware ordering. Returns None when at least one advisory
    declares no fix (no single upgrade clears the group) or when any version
    string fails to parse as a Version (mixing semver schemes; caller falls
    back to a 'see findings' message).
    """
    fixed_versions: list[str] = []
    for f in findings_in_group:
        adv = advisory_index.get(f.advisory_id)
        if not adv:
            return None
        v = _fixed_in_for_finding(f, adv)
        if v is None:
            return None
        fixed_versions.append(v)
    if not fixed_versions:
        return None
    try:
        parsed = [Version(v) for v in fixed_versions]
    except InvalidVersion:
        return None
    return str(max(parsed))


# ── Text renderer ────────────────────────────────────────────────────────────


def render_text(
    findings: list[Finding],
    advisory_index: dict[str, dict],
    stats: ScanStats,
    *,
    use_color: bool = False,
    verbose: bool = False,
) -> str:
    """Grouped human-readable output. See module docstring for shape."""
    unit_phrase = _pluralize(stats.unit_count, stats.unit_label)
    component_phrase = _pluralize(stats.component_count, "component")
    if not findings:
        parts = [f"Scanned {unit_phrase}, {component_phrase}"]
        if stats.parse_failed:
            parts.append(f"({stats.parse_failed} failed to parse)")
        parts.append("— no findings.")
        return " ".join(parts)

    groups = _group_findings(findings)
    ranked: list[tuple[int, str, GroupKey, list[Finding]]] = []
    for key, group_findings in groups.items():
        max_rank = max(_severity_rank(_label_for(f, advisory_index)) for f in group_findings)
        ranked.append((max_rank, key[1], key, group_findings))
    # Severity desc, then component name asc, then version asc (stable).
    ranked.sort(key=lambda t: (-t[0], t[1]))

    out: list[str] = []
    n_pkgs = len(groups)
    out.append(
        f"Found {len(findings)} "
        f"vulnerabilit{'y' if len(findings) == 1 else 'ies'} in "
        f"{n_pkgs} package{'' if n_pkgs == 1 else 's'}."
    )
    out.append("")

    for _, _, key, group_findings in ranked:
        _, _, _, source_manifest = key
        first = group_findings[0]
        name_part, version_part = _component_label(first.component)
        if version_part:
            out.append(f"{name_part} {version_part}")
        else:
            out.append(name_part)

        out.append(f"  location: {source_manifest}")

        attributed = first.attributed_to
        if attributed:
            out.append(f"  via:      {attributed}")
            out.append(f"  fix:      upgrade or remove {attributed}")
        else:
            agg = _aggregate_fix(group_findings, advisory_index)
            if agg is not None:
                out.append(f"  fix:      upgrade to >={agg}")
            else:
                out.append("  fix:      see findings")
        out.append("")

        findings_sorted = sorted(
            group_findings,
            key=lambda f: (-_severity_rank(_label_for(f, advisory_index)), f.advisory_id),
        )
        for f in findings_sorted:
            adv = advisory_index.get(f.advisory_id) or {}
            label = derive_severity_label(adv)
            label_disp = _color(label, use_color)
            fixed_in = _fixed_in_for_finding(f, adv) or "no fix"
            summary = _summary_for_advisory(adv)
            source = _source_for_advisory(adv) or "asve.dev"
            out.append(
                f"  {label_disp}  {f.advisory_id}  fixed in {fixed_in}  {summary}  [{source}]"
            )
            if verbose:
                ds_asve = (adv.get("database_specific") or {}).get("asve") or {}
                if not isinstance(ds_asve, dict):
                    ds_asve = {}
                surfaces = ds_asve.get("surfaces")
                if isinstance(surfaces, list) and surfaces:
                    out.append(f"        surfaces: {', '.join(str(s) for s in surfaces)}")
                agent_impact = ds_asve.get("agent_impact") or {}
                if isinstance(agent_impact, dict):
                    impacts = [k for k, v in agent_impact.items() if v]
                    if impacts:
                        out.append(f"        agent_impact: {', '.join(impacts)}")
                out.append(f"        confidence: {f.confidence}")
        out.append("")

    sources_str = " + ".join(sorted(stats.sources)) if stats.sources else "(none)"
    parse_note = f" ({stats.parse_failed} failed to parse)" if stats.parse_failed else ""
    out.append(f"Scanned {unit_phrase}, {component_phrase}{parse_note}. Sources: {sources_str}.")
    return "\n".join(out)


def _pluralize(count: int, label: str) -> str:
    """`1 manifest` vs `2 manifests`. Handles trailing-s correctly for the
    two labels we use (`manifest`, `active plugin`, `component`)."""
    if count == 1:
        return f"{count} {label}"
    return f"{count} {label}s"


# ── GitHub workflow-command renderer ─────────────────────────────────────────


def _esc_param(value: str) -> str:
    """Percent-encode a workflow-command parameter value per GitHub docs."""
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _esc_data(value: str) -> str:
    """Percent-encode a workflow-command message value per GitHub docs."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def render_github(findings: list[Finding]) -> str:
    """Emit one workflow-annotation line per finding. Matcher `confidence`
    maps to `::error` (high) or `::warning` (low/unknown). Returns the joined
    string; caller prints to stdout."""
    level_for = {"high": "error", "low": "warning", "unknown": "warning"}
    lines: list[str] = []
    for f in findings:
        kind = level_for.get(f.confidence, "warning")
        file_param = _esc_param(str(f.component.source_manifest))
        title_param = _esc_param(f.advisory_id)
        message = f.reason or f.advisory_id
        if f.attributed_to:
            message = f"{message} (via {f.attributed_to})"
        lines.append(f"::{kind} file={file_param},title={title_param}::{_esc_data(message)}")
    return "\n".join(lines)


# ── JSON renderer ────────────────────────────────────────────────────────────


def render_json(findings: list[Finding], advisory_index: dict[str, dict], stats: ScanStats) -> str:
    """Structured per-finding records + scan-level stats. The schema is
    documented in README; consumers should treat unknown keys as forward-
    compatible additions, not as a stability break."""
    out_findings = []
    for f in findings:
        adv = advisory_index.get(f.advisory_id) or {}
        out_findings.append(
            {
                "id": f.advisory_id,
                "severity": derive_severity_label(adv),
                "score": derive_severity_score(adv),
                "confidence": f.confidence,
                "package": {
                    "ecosystem": f.component.ecosystem,
                    "name": f.component.name,
                    "version": f.component.version,
                },
                "location": str(f.component.source_manifest),
                "fixed_in": _fixed_in_for_finding(f, adv),
                "summary": _summary_for_advisory(adv) or None,
                "source": _source_for_advisory(adv),
                "attributed_to": f.attributed_to,
            }
        )
    document = {
        "findings": out_findings,
        "stats": {
            "unit": stats.unit_label,
            "units": stats.unit_count,
            "components": stats.component_count,
            "parse_failed": stats.parse_failed,
            "high_confidence": sum(1 for f in findings if f.confidence == "high"),
            "sources": sorted(stats.sources),
        },
    }
    return json.dumps(document, indent=2, sort_keys=False)
