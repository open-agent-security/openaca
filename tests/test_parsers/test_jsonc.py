"""JSON with comments and trailing commas, shared by Cursor's `permissions.json`
posture read and Devin CLI's configuration files."""

from __future__ import annotations

import pytest

from tools.parsers import jsonc


def test_line_and_block_comments_and_trailing_commas_are_accepted():
    text = """{
      // line comment
      "a": [1, 2,], /* block
      comment */
      "b": {"c": "d",},
    }"""

    assert jsonc.loads(text) == {"a": [1, 2], "b": {"c": "d"}}


def test_comment_and_comma_syntax_inside_strings_is_preserved():
    text = '{"url": "https://x.example/a//b", "glob": "/* not a comment */", "s": ",}"}'

    assert jsonc.loads(text) == {
        "url": "https://x.example/a//b",
        "glob": "/* not a comment */",
        "s": ",}",
    }


def test_escaped_quote_does_not_end_a_string():
    assert jsonc.loads(r'{"a": "say \"// hi\"", }') == {"a": 'say "// hi"'}


def test_anything_else_that_is_not_json_still_raises():
    with pytest.raises(ValueError):
        jsonc.loads("{'single': 'quotes'}")


def test_load_path_reads_a_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"permissions": {"allow": ["Exec(git)"],},}  // trailing\n')

    assert jsonc.load_path(path) == {"permissions": {"allow": ["Exec(git)"]}}
