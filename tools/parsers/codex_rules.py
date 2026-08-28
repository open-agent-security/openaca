"""Codex's command-approval DSL, `<root>/rules/*.rules` (plan 043 Task 3).

This is **posture, not composition**: the file declares no components, it
declares which shell commands may run without asking. It therefore emits no
`ComponentRef` — `tools/posture/rules/command_policy_allow.py` reads it.

    prefix_rule(pattern=["git", "commit"], decision="allow")

## What the evidence establishes, and what it does not

Plan 043 gated this parser on confirming statement granularity before writing
a regex, against the audited binary or a real sample. The binary's
`execpolicy check` entry point is not reachable as a CLI subcommand in
0.147.0, so the evidence is the sample: every statement in the audited
endpoint's `rules/default.rules` is one complete `prefix_rule(...)` call on
one physical line, with no multi-line calls and no trailing commas.

Three limits follow, and they shape the implementation:

1. **The regex runs over the whole file, not line by line.** The sample does
   not contain a multi-line call, which is not the same as the grammar
   forbidding one. A whole-file pattern matches either shape, so a real
   multi-line call counts as one rule rather than several unparsed lines.
2. **Comment syntax is unverified.** The sample contains no comments, so
   nothing here strips them. A `prefix_rule(...)` written inside a comment
   would therefore be extracted and reported as an active allow-rule. That is
   over-reporting, which is the safe direction for a security tool and the
   direction this project takes elsewhere — but it is a known imprecision,
   not an oversight.
3. **Anything not consumed by a match is counted, never guessed at.**
   `unparsed_count` is the number of non-whitespace regions falling outside a
   matched call, so an unrecognised rule verb degrades to "we could not read
   this" and surfaces as a coverage gap. It never degrades to a wrong
   allow/deny, which would become a wrong posture finding.

No general expression evaluator, deliberately. The DSL is incompletely
specified, and a permissive parser would manufacture confident answers about
a grammar nobody here has seen written down.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple


class PrefixRule(NamedTuple):
    """One `prefix_rule(pattern=[...], decision="...")` call."""

    pattern: tuple[str, ...]
    decision: str


class ParsedRules(NamedTuple):
    """The one result shape every caller consumes.

    Carrying `unparsed_count` alongside the rules — rather than returning a
    bare list — is what lets a caller report "this file had content we could
    not read" instead of silently presenting a partial read as complete.
    """

    rules: list[PrefixRule]
    unparsed_count: int


# One `prefix_rule(...)` call. `re.DOTALL` is deliberately NOT set: the
# argument list may span lines, which `\s` already covers, but a dot that
# crosses newlines would let one malformed call swallow the rest of the file.
_CALL = re.compile(
    r"""prefix_rule\s*\(\s*
        pattern\s*=\s*\[(?P<pattern>[^\]]*)\]\s*,\s*
        decision\s*=\s*(?P<q>["'])(?P<decision>[^"']*)(?P=q)\s*
        ,?\s*\)""",
    re.VERBOSE,
)

_STRING_ITEM = re.compile(r"""(["'])(?P<value>(?:\\.|(?!\1).)*)\1""")

# A quoted string with no capturing group, so it can be repeated inside
# `_PATTERN_LIST` without Python's re module rejecting a duplicate group name.
_QUOTED_STRING = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""

# The complete grammar for a pattern-list body: zero or more quoted strings,
# comma-separated, with an optional trailing comma, and nothing else. Used to
# reject a call whose pattern list contains anything this parser cannot read
# (an unquoted token, a stray symbol) rather than silently keeping only the
# quoted items it does recognise.
_PATTERN_LIST = re.compile(
    rf"""^\s*(?:{_QUOTED_STRING}\s*(?:,\s*{_QUOTED_STRING}\s*)*,?\s*)?$""",
    re.VERBOSE,
)


def _pattern_items(raw: str) -> tuple[str, ...] | None:
    """Return the pattern list's items, or `None` if the body is not fully readable."""
    if _PATTERN_LIST.match(raw) is None:
        return None
    return tuple(m.group("value") for m in _STRING_ITEM.finditer(raw))


def parse_rules(path: Path) -> ParsedRules:
    """Read one `.rules` file.

    A missing or unreadable file is `ParsedRules([], 0)` rather than an error:
    the approval surface being absent is a normal state, not a parse failure.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ParsedRules([], 0)

    rules: list[PrefixRule] = []
    spans: list[tuple[int, int]] = []
    for match in _CALL.finditer(text):
        items = _pattern_items(match.group("pattern"))
        if not items:
            # A call whose pattern list we could not read is not a rule we can
            # report on. Leave its span unconsumed so it counts as unparsed.
            continue
        rules.append(PrefixRule(items, match.group("decision")))
        spans.append(match.span())

    return ParsedRules(rules, _unparsed_regions(text, spans))


def _unparsed_regions(text: str, spans: list[tuple[int, int]]) -> int:
    """Count non-whitespace regions of `text` outside any matched call.

    Regions, not lines: the sample could not establish that one physical line
    is the statement unit, so counting lines would assert a granularity the
    evidence does not support.
    """
    cursor = 0
    count = 0
    for start, end in sorted(spans):
        if text[cursor:start].strip():
            count += 1
        cursor = max(cursor, end)
    if text[cursor:].strip():
        count += 1
    return count
