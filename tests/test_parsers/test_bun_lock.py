from __future__ import annotations

from pathlib import Path

from tools.parsers.bun_lock import _collect_runtime_keys, _strip_trailing_commas, parse


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
    "hono": ["hono@4.12.5", "", { "ms": "2.1.3" }, "sha512-abc=="],
    "ms": ["ms@2.1.3", "", {}, "sha512-ghi=="],
    "@types/node": ["@types/node@20.0.0", "", {}, "sha512-dev=="],
  },
}
""",
        encoding="utf-8",
    )
    refs = parse(lock)
    by_name = {r.name: r for r in refs}
    # hono is a direct runtime dep; ms is a transitive dep of hono → both emitted
    assert set(by_name) == {"hono", "ms"}
    assert by_name["hono"].version == "4.12.5"
    assert by_name["hono"].ecosystem == "npm"
    assert by_name["hono"].extra["transitive"] is True
    assert by_name["ms"].version == "2.1.3"
    # @types/node is in packages but not reachable from runtime deps → skipped


def test_parse_skips_dev_only_deps(tmp_path: Path):
    lock = tmp_path / "bun.lock"
    lock.write_text(
        """{
  "lockfileVersion": 1,
  "workspaces": {
    "": {
      "name": "host-pkg",
      "dependencies": { "express": "^4" },
      "devDependencies": { "typescript": "^5", "jest": "^29" },
    },
  },
  "packages": {
    "express": ["express@4.18.0", "", {}, "sha512-abc=="],
    "typescript": ["typescript@5.0.0", "", {}, "sha512-def=="],
    "jest": ["jest@29.0.0", "", {}, "sha512-ghi=="],
  },
}
""",
        encoding="utf-8",
    )
    refs = parse(lock)
    names = {r.name for r in refs}
    assert "express" in names
    assert "typescript" not in names
    assert "jest" not in names


def test_parse_no_workspaces_emits_all(tmp_path: Path):
    # When workspaces is absent we cannot distinguish dev from runtime; emit everything.
    lock = tmp_path / "bun.lock"
    lock.write_text(
        """{
  "lockfileVersion": 1,
  "packages": {
    "hono": ["hono@4.12.5", "", {}, "sha512-abc=="],
    "@discordjs/builders": ["@discordjs/builders@1.13.1", "", {}, "sha512-def=="],
  },
}
""",
        encoding="utf-8",
    )
    refs = parse(lock)
    assert {r.name for r in refs} == {"hono", "@discordjs/builders"}


def test_collect_runtime_keys_bfs(tmp_path: Path):
    packages = {
        "express": ["express@4.18.0", "", {"body-parser": "1.20.0"}, "sha512-a=="],
        "body-parser": ["body-parser@1.20.0", "", {}, "sha512-b=="],
        "typescript": ["typescript@5.0.0", "", {}, "sha512-c=="],
    }
    workspaces = {"": {"dependencies": {"express": "^4"}, "devDependencies": {"typescript": "^5"}}}
    keys = _collect_runtime_keys(packages, workspaces)
    assert keys == {"express", "body-parser"}


def test_collect_runtime_keys_no_runtime_seeds_returns_none():
    packages = {"typescript": ["typescript@5.0.0", "", {}, "sha512-c=="]}
    workspaces = {"": {"devDependencies": {"typescript": "^5"}}}
    assert _collect_runtime_keys(packages, workspaces) is None


def test_parse_malformed_returns_empty(tmp_path: Path):
    bad = tmp_path / "bun.lock"
    bad.write_text("this is not a lockfile {{{", encoding="utf-8")
    assert parse(bad) == []


def test_parse_missing_packages_returns_empty(tmp_path: Path):
    lock = tmp_path / "bun.lock"
    lock.write_text('{"lockfileVersion": 1}', encoding="utf-8")
    assert parse(lock) == []
