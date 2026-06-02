from __future__ import annotations

from pathlib import Path

from tools.parsers.bun_lock import _strip_trailing_commas, parse


def test_strip_trailing_comma_in_object():
    assert _strip_trailing_commas('{"a": 1,}') == '{"a": 1}'


def test_strip_trailing_comma_in_array():
    assert _strip_trailing_commas("[1, 2, ]") == "[1, 2 ]"


def test_comma_before_brace_inside_string_is_preserved():
    # A literal "...,}" inside a string value must NOT be touched.
    src = '{"a": "x,}"}'
    assert _strip_trailing_commas(src) == src


def test_escaped_quote_does_not_break_string_state():
    # The escaped quote keeps us inside the string, so the ",]" stays literal.
    src = '{"a": "he\\",]"}'
    assert _strip_trailing_commas(src) == src


def test_parse_extracts_pinned_versions_and_skips_root(tmp_path: Path):
    lock = tmp_path / "bun.lock"
    lock.write_text(
        """{
  "lockfileVersion": 1,
  "workspaces": {
    "": { "name": "host-pkg", "dependencies": { "hono": "^4" }, },
  },
  "packages": {
    "hono": ["hono@4.12.5", "", {}, "sha512-abc=="],
    "@discordjs/builders": ["@discordjs/builders@1.13.1", "", {}, "sha512-def=="],
  },
}
""",
        encoding="utf-8",
    )
    refs = parse(lock)
    by_name = {r.name: r for r in refs}
    assert set(by_name) == {"hono", "@discordjs/builders"}
    assert by_name["hono"].version == "4.12.5"
    assert by_name["hono"].ecosystem == "npm"
    assert by_name["hono"].extra["transitive"] is True
    assert by_name["@discordjs/builders"].version == "1.13.1"  # scoped name preserved


def test_parse_malformed_returns_empty(tmp_path: Path):
    bad = tmp_path / "bun.lock"
    bad.write_text("this is not a lockfile {{{", encoding="utf-8")
    assert parse(bad) == []


def test_parse_missing_packages_returns_empty(tmp_path: Path):
    lock = tmp_path / "bun.lock"
    lock.write_text('{"lockfileVersion": 1}', encoding="utf-8")
    assert parse(lock) == []
