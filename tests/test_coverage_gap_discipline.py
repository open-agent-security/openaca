"""Enforcement for the coverage rule (plan 043 review, theme 5).

`composition_coverage` says whether we identified the agent's components, and
gap recording is **opt-in** — a plain `warnings.append` is a note. That keeps
`complete` reachable, at the cost that a path which drops a component must
remember `record_gap`.

Relying on memory is exactly what failed: after the opt-in change, seven paths
across `graph_build`, `claude_install`, and `claude_plugin_root` kept dropping
components while leaving coverage `complete`, and a reviewer found them rather
than a test. This module makes the rule enforced instead.

The heuristic is deliberately structural rather than message-based — matching on
warning text is what produced the original mistake, since it classified by what
a warning *said* instead of what its code path *did*.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"

# Modules that build composition. A warning here can mean a missing component;
# elsewhere (CLI, render, federation) warnings are user-facing diagnostics.
_COMPOSITION_MODULES = (
    "graph_build.py",
    "graph_build_cursor.py",
    "parsers/claude_install.py",
    "parsers/claude_plugin_root.py",
    "cursor_subagents.py",
    "cursor_commands.py",
)

# How far past a warning to look for the statement that drops the component.
_LOOKAHEAD = 7

_DROPS = re.compile(r"\n\s*(continue|return)\b")

# Sites that warn near a drop but do not themselves drop a component.
# Each needs a reason, so an addition here is a decision rather than a silencer.
_ALLOWED: dict[tuple[str, str], str] = {
    (
        "parsers/claude_install.py",
        'warnings.append(f"{plugin_key}: {w}")',
    ): "Forwards a sub-walk's warnings verbatim; the drop, if any, happened in "
    "the callee, which records its own gap.",
}


def _offenders() -> list[str]:
    out: list[str] = []
    for rel in _COMPOSITION_MODULES:
        path = TOOLS / rel
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            if "warnings.append(" not in line or "record_gap" in line:
                continue
            window = "\n".join(lines[i : i + _LOOKAHEAD])
            if not _DROPS.search(window):
                continue
            if (rel, line.strip()) in _ALLOWED:
                continue
            out.append(f"{rel}:{i + 1}: {line.strip()}")
    return out


def test_component_dropping_paths_record_a_gap():
    """A path that drops a component must lower coverage.

    If this fails, the flagged site warns and then `continue`s or `return`s
    past a component. Either call `record_gap(warnings, ...)` instead of
    `warnings.append(...)`, or — if it genuinely drops nothing — add it to
    `_ALLOWED` with the reason.
    """
    offenders = _offenders()

    assert not offenders, (
        "these paths drop a component without recording a coverage gap, so a "
        "partial inventory would be reported as `complete`:\n  " + "\n  ".join(offenders)
    )


def test_the_check_can_actually_fail(tmp_path):
    """A guard that cannot fail guards nothing."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        'def f(warnings):\n    warnings.append("dropped it")\n    return []\n', encoding="utf-8"
    )
    lines = sample.read_text().split("\n")
    hits = [
        i
        for i, line in enumerate(lines)
        if "warnings.append(" in line and _DROPS.search("\n".join(lines[i : i + _LOOKAHEAD]))
    ]

    assert hits, "the detector must recognise an append followed by a return"


@pytest.mark.parametrize("rel", _COMPOSITION_MODULES)
def test_every_scanned_module_exists(rel):
    """A typo'd path would silently scan nothing and pass forever."""
    assert (TOOLS / rel).exists(), f"{rel} is listed but missing — the check would skip it"
