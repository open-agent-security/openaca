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


def test_missing_name_raises(tmp_path):
    """developers.openai.com/codex/agent-configuration/subagents: every
    standalone agent file "must define: `name`, `description`,
    `developer_instructions`" — Codex itself rejects one that doesn't, so
    this must not silently fall back to the filename and inventory a
    component Codex never loads.
    """
    p = tmp_path / "reviewer.toml"
    p.write_text(
        'description = "no name key"\ndeveloper_instructions = "do stuff"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="name"):
        codex_agent.parse(p)


def test_a_non_string_name_raises(tmp_path):
    p = tmp_path / "odd.toml"
    p.write_text(
        'name = 42\ndescription = "d"\ndeveloper_instructions = "i"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="name"):
        codex_agent.parse(p)


def test_missing_description_raises(tmp_path):
    p = tmp_path / "reviewer.toml"
    p.write_text('name = "reviewer"\ndeveloper_instructions = "do stuff"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="description"):
        codex_agent.parse(p)


def test_missing_developer_instructions_raises(tmp_path):
    p = tmp_path / "reviewer.toml"
    p.write_text('name = "reviewer"\ndescription = "d"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="developer_instructions"):
        codex_agent.parse(p)


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


def test_an_empty_file_raises(tmp_path):
    """An empty file defines none of the three required fields, so Codex
    rejects it — same as a file missing just one of them.
    """
    p = tmp_path / "blank.toml"
    p.write_text("", encoding="utf-8")

    with pytest.raises(ValueError):
        codex_agent.parse(p)
