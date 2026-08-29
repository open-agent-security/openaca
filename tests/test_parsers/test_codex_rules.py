"""Codex's `.rules` approval DSL (plan 043 Task 3).

The parser is deliberately conservative: content it does not recognise is
counted as unparsed, never guessed at, because a wrong `PrefixRule` becomes a
wrong posture finding while a wrong count is only a coverage gap.
"""

from __future__ import annotations

import os

import pytest

from tools.parsers.codex_rules import ParsedRules, PrefixRule, parse_rules


def test_a_single_rule_parses(tmp_path):
    p = tmp_path / "default.rules"
    p.write_text('prefix_rule(pattern=["git", "commit"], decision="allow")\n', encoding="utf-8")

    assert parse_rules(p) == ParsedRules([PrefixRule(("git", "commit"), "allow")], 0)


def test_many_rules_parse_with_nothing_unparsed(tmp_path):
    p = tmp_path / "default.rules"
    p.write_text(
        'prefix_rule(pattern=["uv", "run", "pytest"], decision="allow")\n'
        'prefix_rule(pattern=["git", "diff"], decision="deny")\n',
        encoding="utf-8",
    )

    parsed = parse_rules(p)

    assert parsed.unparsed_count == 0
    assert parsed.rules == [
        PrefixRule(("uv", "run", "pytest"), "allow"),
        PrefixRule(("git", "diff"), "deny"),
    ]


def test_a_multi_line_call_is_one_rule_not_several_unparsed_lines(tmp_path):
    """The sample could not prove one-call-per-line, so the regex spans lines."""
    p = tmp_path / "default.rules"
    p.write_text(
        'prefix_rule(\n    pattern=["git", "push"],\n    decision="allow"\n)\n',
        encoding="utf-8",
    )

    assert parse_rules(p) == ParsedRules([PrefixRule(("git", "push"), "allow")], 0)


def test_an_unrecognised_verb_is_counted_not_guessed(tmp_path):
    """Degrading to 'unparsed' is safe; degrading to a wrong decision is not."""
    p = tmp_path / "default.rules"
    p.write_text(
        'prefix_rule(pattern=["git"], decision="allow")\nsuffix_rule(whatever=1)\n',
        encoding="utf-8",
    )

    parsed = parse_rules(p)

    assert parsed.rules == [PrefixRule(("git",), "allow")]
    assert parsed.unparsed_count == 1


def test_empty_file_yields_no_rules_and_no_gap(tmp_path):
    p = tmp_path / "default.rules"
    p.write_text("", encoding="utf-8")

    assert parse_rules(p) == ParsedRules([], 0)


def test_whitespace_only_file_yields_no_gap(tmp_path):
    """Must not trip a coverage warning for a file with nothing in it."""
    p = tmp_path / "default.rules"
    p.write_text("\n\n   \n\t\n", encoding="utf-8")

    assert parse_rules(p) == ParsedRules([], 0)


def test_a_missing_file_is_not_an_error(tmp_path):
    """An absent approval surface is a normal state, not a parse failure."""
    assert parse_rules(tmp_path / "absent.rules") == ParsedRules([], 0)


def test_an_unreadable_existing_file_counts_as_a_gap(tmp_path):
    """Unlike a missing file, a present-but-unreadable one must not read as clean.

    Both callers reach this function via `rules_dir.glob("*.rules")`, so the
    file's existence is already confirmed by the time `parse_rules` runs — a
    permission error here is not "nothing to see," it is content we know
    exists and cannot read.
    """
    p = tmp_path / "default.rules"
    p.write_text('prefix_rule(pattern=["git"], decision="allow")\n', encoding="utf-8")
    p.chmod(0o000)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("cannot deny read access to self as the current user")
        assert parse_rules(p) == ParsedRules([], 1)
    finally:
        p.chmod(0o644)


def test_a_call_with_an_unreadable_pattern_list_counts_as_unparsed(tmp_path):
    p = tmp_path / "default.rules"
    p.write_text('prefix_rule(pattern=[], decision="allow")\n', encoding="utf-8")

    parsed = parse_rules(p)

    assert parsed.rules == []
    assert parsed.unparsed_count == 1


def test_a_pattern_list_with_an_unquoted_item_is_rejected_atomically(tmp_path):
    """A partially unreadable pattern list must not narrow to the quoted items.

    `["git", unknown]` must not become `PrefixRule(("git",), "allow")` — that
    would report a broader allow than the file actually declares.
    """
    p = tmp_path / "default.rules"
    p.write_text('prefix_rule(pattern=["git", unknown], decision="allow")\n', encoding="utf-8")

    parsed = parse_rules(p)

    assert parsed.rules == []
    assert parsed.unparsed_count == 1


def test_the_real_endpoint_sample_parses_cleanly(tmp_path):
    """Regression pin for the shape the audited endpoint actually ships."""
    p = tmp_path / "default.rules"
    p.write_text(
        'prefix_rule(pattern=["uv", "sync"], decision="allow")\n'
        'prefix_rule(pattern=["uv", "run", "pytest"], decision="allow")\n'
        'prefix_rule(pattern=["git", "diff", "--check"], decision="allow")\n'
        'prefix_rule(pattern=["git", "commit", "-m"], decision="allow")\n',
        encoding="utf-8",
    )

    parsed = parse_rules(p)

    assert len(parsed.rules) == 4
    assert parsed.unparsed_count == 0
    assert all(r.decision == "allow" for r in parsed.rules)
