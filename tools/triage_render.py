"""Renderers for OpenACA exposure triage reports."""

from __future__ import annotations

import json
from typing import Any, Literal

from tools.triage import ExposureCard, ExposurePathNode

TriageFormat = Literal["text", "markdown", "json"]

_MARKDOWN_ESCAPE_CHARS = set("\\`*_{}[]()#+-.!|<>~")


def render_triage_report(
    cards: list[ExposureCard], scan_doc: dict[str, Any], *, output_format: TriageFormat
) -> str:
    if output_format == "json":
        return render_triage_json(cards, scan_doc)
    if output_format == "markdown":
        return render_triage_markdown(cards, scan_doc)
    return render_triage_text(cards, scan_doc)


def render_triage_json(cards: list[ExposureCard], scan_doc: dict[str, Any]) -> str:
    return json.dumps(
        {
            "report_type": "exposure",
            "target": scan_doc.get("target") or {},
            "stats": scan_doc.get("stats") or {},
            "cards": [card.to_dict() for card in cards],
            "scope_limits": _report_scope_limits(cards),
        },
        indent=2,
    )


def render_triage_text(cards: list[ExposureCard], scan_doc: dict[str, Any]) -> str:
    lines = ["Exposure report", ""]
    lines.extend(_target_lines(scan_doc, bullet=""))
    if len(lines) > 2:
        lines.append("")
    lines.append(_summary_line(cards, scan_doc))
    if not cards:
        lines.extend(["", "No exposure cards were generated from this scan."])
    for card in cards[:5]:
        lines.extend(
            [
                "",
                f"{card.rank}. {card.priority.upper()} - {card.component.name}",
                f"   type: {card.component.type}",
                f"   path: {_path_label(_primary_path(card))}",
                f"   occurrences: {len(card.occurrences)}",
                f"   evidence: {_evidence_summary(card)}",
                f"   why: {card.why_it_matters}",
                f"   action: {card.action}",
                f"   confidence: {card.confidence}",
            ]
        )
        for limit in card.scope_limits:
            lines.append(f"   scope: {limit}")
    lines.extend(["", "What we could not see"])
    lines.extend(f"- {limit}" for limit in _report_scope_limits(cards))
    lines.extend(["", "Suggested next step", "- Review the top card owner and apply the action."])
    return "\n".join(lines)


def render_triage_markdown(cards: list[ExposureCard], scan_doc: dict[str, Any]) -> str:
    lines = ["# OpenACA Exposure Report", ""]
    target_lines = _target_lines(scan_doc, bullet="- ", escape=True)
    if target_lines:
        lines.extend(["## Target", *target_lines, ""])
    lines.extend(["## Summary", "", f"- {_summary_line(cards, scan_doc)}", ""])
    lines.extend(["## Top Exposures", ""])
    if not cards:
        lines.extend(["No exposure cards were generated from this scan.", ""])
    for card in cards[:5]:
        label = _escape_markdown(card.component.name)
        lines.extend(
            [
                f"### {card.rank}. {card.priority.upper()} - {label}",
                "",
                f"- Type: `{_code_span_safe(card.component.type)}`",
                f"- Path: `{_path_label_code_span(_primary_path(card))}`",
                f"- Occurrences: `{len(card.occurrences)}`",
                f"- Evidence: {_evidence_summary(card, escape=True)}",
                f"- Action: `{card.action}`",
                f"- Confidence: `{card.confidence}`",
                "",
                _escape_markdown(card.why_it_matters),
                "",
            ]
        )
        if card.scope_limits:
            lines.append("Scope limits:")
            lines.extend(f"- {limit}" for limit in card.scope_limits)
            lines.append("")
    lines.extend(["## What we could not see", ""])
    lines.extend(f"- {limit}" for limit in _report_scope_limits(cards))
    lines.extend(
        ["", "## Suggested next step", "", "Review the top card owner and apply the action."]
    )
    return "\n".join(lines).rstrip() + "\n"


def _target_lines(scan_doc: dict[str, Any], *, bullet: str, escape: bool = False) -> list[str]:
    target = scan_doc.get("target")
    if not isinstance(target, dict):
        return []

    def _clean(text: str) -> str:
        return _escape_markdown(text) if escape else text

    lines: list[str] = []
    host_surface = target.get("host_surface")
    if isinstance(host_surface, str) and host_surface:
        lines.append(f"{bullet}Surface: {_clean(host_surface)}")
    rows = target.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = row.get("label")
            value = row.get("value")
            if isinstance(label, str) and isinstance(value, str):
                lines.append(f"{bullet}{_clean(label)}: {_clean(value)}")
    return lines


def _summary_line(cards: list[ExposureCard], scan_doc: dict[str, Any]) -> str:
    stats = scan_doc.get("stats") if isinstance(scan_doc.get("stats"), dict) else {}
    finding_count = len(scan_doc.get("findings") or [])
    component_count = stats.get("components") if isinstance(stats, dict) else None
    if isinstance(component_count, int):
        return (
            f"{len(cards)} exposure card(s) from {finding_count} finding(s) "
            f"across {component_count} component(s)."
        )
    return f"{len(cards)} exposure card(s) from {finding_count} finding(s)."


def _evidence_summary(card: ExposureCard, *, escape: bool = False) -> str:
    def _clean(text: str) -> str:
        return _escape_markdown(text) if escape else text

    return ", ".join(
        f"{_clean(item.id)} ({item.severity}, {item.provenance})" for item in card.evidence
    )


def _primary_path(card: ExposureCard) -> list[ExposurePathNode]:
    return card.occurrences[0].composition_paths[0]


def _path_label(path: list[ExposurePathNode]) -> str:
    return " -> ".join(f"{item.type} {item.name}" for item in path) or "<unknown>"


def _path_label_code_span(path: list[ExposurePathNode]) -> str:
    """Render a composition path for a single-backtick Markdown code span.

    CommonMark doesn't honor backslash escapes inside a code span, so a
    scan-derived name containing a backtick must be neutralized directly
    rather than escaped, or it would close the span early.
    """
    if not path:
        return "<unknown>"
    return " -> ".join(
        f"{_code_span_safe(item.type)} {_code_span_safe(item.name)}" for item in path
    )


def _code_span_safe(value: str) -> str:
    return " ".join(value.splitlines()).replace("`", "'")


def _escape_markdown(text: str) -> str:
    """Neutralize Markdown control characters and line breaks in scan-derived
    text so a crafted component name or path can't spoof headings or
    formatting in a forwarded report."""
    collapsed = " ".join(text.splitlines())
    return "".join(f"\\{ch}" if ch in _MARKDOWN_ESCAPE_CHARS else ch for ch in collapsed)


def _report_scope_limits(cards: list[ExposureCard]) -> list[str]:
    limits = {
        "Static composition report; runtime behavior was not observed.",
        "Exposure priority uses scan evidence only and is not proof of exploitability.",
    }
    for card in cards:
        limits.update(card.scope_limits)
    return sorted(limits)
