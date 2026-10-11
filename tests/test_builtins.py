"""An agent's own sub-agents and skills are listed on its BOM root (ADR-0076).

They are the agent, not its composition: nothing installed them and nothing
can remove them, so they never become components, and no posture rule, policy
or coverage count ever sees them. What a consumer needs is to tell a call to
one of them from a call to something absent, and the root says which names
those are.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tools.agent_kinds import REGISTRY, AgentInstance, Builtins, builtins_of, kind_for
from tools.bom import AGENT_ROOT_PREFIX, AgentBOM
from tools.bom_lint import lint_bom, load_schema


def _installed(kind_id: str, config_root: Path) -> AgentInstance:
    kind = kind_for(kind_id)
    return AgentInstance(
        kind_id=kind_id,
        display_name=kind.display_name,
        source="installed",
        root_label=kind.root_label,
        coverage_baseline=kind.coverage_baseline["installed"],
        config_root=config_root,
    )


def _lint(document: dict) -> list[str]:
    return lint_bom(document, Draft202012Validator(load_schema()))


def _root_properties(document: dict) -> dict[str, str]:
    return {p["name"]: p["value"] for p in document["metadata"]["component"]["properties"]}


def _agent_bom(builtins: Builtins) -> dict:
    return AgentBOM(
        components=[],
        edges=[],
        target_bom_ref=f"{AGENT_ROOT_PREFIX}claude-code",
        agent_kind="claude-code",
        composition_source="installed",
        composition_coverage="complete",
        builtin_agents=builtins.agents,
        builtin_skills=builtins.skills,
    ).to_cyclonedx()


def test_claude_code_names_its_own_subagent_types_and_skills(tmp_path):
    builtins = builtins_of(_installed("claude-code", tmp_path))

    assert {"general-purpose", "Explore", "Plan", "claude-code-guide"} <= set(builtins.agents)
    assert {"verify", "artifact-design", "simplify"} <= set(builtins.skills)


def test_devin_names_its_subagent_profiles_and_skills(tmp_path):
    builtins = builtins_of(_installed("devin", tmp_path))

    assert {"subagent_explore", "subagent_general"} <= set(builtins.agents)
    # A real Devin session called `devin-cli` and read as absent until it was
    # listed here.
    assert set(builtins.skills) == {"declarative-repo-setup", "devin-cli", "upload-secrets"}


def test_codex_reads_its_skills_from_the_marked_root_not_a_list(tmp_path):
    system = tmp_path / "skills" / ".system"
    (system / "dir-name").mkdir(parents=True)
    (system / ".codex-system-skills.marker").write_text("", encoding="utf-8")
    (system / "dir-name" / "SKILL.md").write_text(
        "---\nname: frontmatter-name\n---\nVendor.\n", encoding="utf-8"
    )
    unmarked = tmp_path / "skills" / "mine"
    unmarked.mkdir(parents=True)
    (unmarked / "SKILL.md").write_text("---\nname: mine\n---\nUser.\n", encoding="utf-8")

    builtins = builtins_of(_installed("codex", tmp_path))

    assert builtins.skills == ("frontmatter-name",)
    assert {"default", "explorer", "worker"} <= set(builtins.agents)


def test_cursor_reads_its_skills_from_its_vendor_root(tmp_path):
    vendor = tmp_path / "skills-cursor" / "create-rule"
    vendor.mkdir(parents=True)
    (vendor / "SKILL.md").write_text("---\nname: create-rule\n---\nVendor.\n", encoding="utf-8")

    assert builtins_of(_installed("cursor", tmp_path)).skills == ("create-rule",)


def test_a_vendor_root_that_is_absent_lists_nothing(tmp_path):
    assert builtins_of(_installed("cursor", tmp_path)) == Builtins()


@pytest.mark.parametrize("kind", [k.id for k in REGISTRY])
def test_a_declared_agent_lists_no_builtins(kind, tmp_path):
    """A repository declares what it adds to an agent. The agent's own
    built-ins are not part of that declaration, whichever kind it is."""
    declared = AgentInstance(
        kind_id=kind,
        display_name=kind,
        source="declared",
        root_label=kind_for(kind).root_label,
        coverage_baseline=kind_for(kind).coverage_baseline["declared"],
        scan_root=tmp_path,
    )

    assert builtins_of(declared) == Builtins()


def test_builtins_are_listed_on_the_root_sorted_and_unique():
    properties = _root_properties(_agent_bom(Builtins(agents=("b", "a", "b"), skills=("z",))))

    assert json.loads(properties["openaca:builtin_agents"]) == ["a", "b"]
    assert json.loads(properties["openaca:builtin_skills"]) == ["z"]


def test_no_builtins_emits_no_property():
    """Absent means none were listed. An empty array would claim a kind was
    asked and answered nothing, which a declared BOM never was."""
    properties = _root_properties(_agent_bom(Builtins()))

    assert "openaca:builtin_agents" not in properties
    assert "openaca:builtin_skills" not in properties


def test_a_bom_listing_builtins_passes_lint():
    assert _lint(_agent_bom(Builtins(agents=("Explore",), skills=("verify",)))) == []


@pytest.mark.parametrize(
    "value",
    ['"Explore"', "[1]", '[""]', "not json", '{"a": 1}', '["a", "a"]'],
)
def test_lint_refuses_a_builtin_list_that_is_not_unique_names(value):
    document = _agent_bom(Builtins(agents=("Explore",)))
    for prop in document["metadata"]["component"]["properties"]:
        if prop["name"] == "openaca:builtin_agents":
            prop["value"] = value

    assert any("openaca:builtin_agents" in error for error in _lint(document))
