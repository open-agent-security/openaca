"""Codex subagents, `<root>/agents/*.toml` (plan 043 Task 2).

Codex's subagents are TOML, not markdown with frontmatter — the one place its
component formats diverge from Claude Code's. The emitted ref shape does not
diverge: identity stays in the `claude-agent/` space so the same subagent
carried by two kinds keys identically.
"""

from __future__ import annotations

import tomllib

import pytest

from tools.parsers import codex_agent

PROBE = """
name = "dummy-probe"
description = "A no-op probe agent."
developer_instructions = "You are a no-op probe agent. Do nothing."
"""


def test_a_subagent_yields_one_agent_ref(tmp_path):
    p = tmp_path / "dummy-probe.toml"
    p.write_text(PROBE, encoding="utf-8")

    refs = codex_agent.parse(p)

    assert len(refs) == 1
    assert refs[0].name == "dummy-probe"
    assert refs[0].extra["component_type"] == "agent"


def test_identity_matches_the_claude_agent_space(tmp_path):
    """Same subagent under two kinds must key identically (ADR-0045)."""
    p = tmp_path / "dummy-probe.toml"
    p.write_text(PROBE, encoding="utf-8")

    assert codex_agent.parse(p)[0].component_identity == "claude-agent/dummy-probe"


def test_name_falls_back_to_the_filename_stem(tmp_path):
    p = tmp_path / "reviewer.toml"
    p.write_text('description = "no name key"\n', encoding="utf-8")

    refs = codex_agent.parse(p)

    assert refs[0].name == "reviewer"
    assert refs[0].component_identity == "claude-agent/reviewer"


def test_a_non_string_name_falls_back_rather_than_emitting_a_bad_identity(tmp_path):
    p = tmp_path / "odd.toml"
    p.write_text("name = 42\n", encoding="utf-8")

    assert codex_agent.parse(p)[0].name == "odd"


def test_malformed_toml_raises(tmp_path):
    p = tmp_path / "broken.toml"
    p.write_text("{ not toml", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        codex_agent.parse(p)


def test_a_non_toml_suffix_yields_nothing(tmp_path):
    """Parity with `claude_command_agent.parse_file`'s extension guard."""
    p = tmp_path / "notes.md"
    p.write_text(PROBE, encoding="utf-8")

    assert codex_agent.parse(p) == []


def test_a_directory_yields_nothing(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()

    assert codex_agent.parse(d) == []


def test_description_is_carried(tmp_path):
    p = tmp_path / "dummy-probe.toml"
    p.write_text(PROBE, encoding="utf-8")

    assert codex_agent.parse(p)[0].extra["description"] == "A no-op probe agent."


def test_an_empty_file_still_yields_a_named_agent(tmp_path):
    """An empty TOML file is a valid, if uninformative, subagent declaration."""
    p = tmp_path / "blank.toml"
    p.write_text("", encoding="utf-8")

    refs = codex_agent.parse(p)

    assert len(refs) == 1
    assert refs[0].name == "blank"
