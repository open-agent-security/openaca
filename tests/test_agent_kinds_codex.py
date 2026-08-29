"""The Codex kind (plan 043 Task 11, ADR-0055/0056)."""

from __future__ import annotations

import json
from pathlib import Path

from tools.agent_kinds import DiscoveryContext, codex

HOOKS = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}}


def _declared(scan_root: Path):
    return codex.discover(DiscoveryContext(source="declared", scan_root=scan_root))


def test_codex_hooks_json_is_evidence(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")

    assert codex.declared_evidence(tmp_path) is not None
    assert len(_declared(tmp_path)) == 1


def test_codex_config_toml_is_evidence(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("[mcp_servers.s]\n", encoding="utf-8")

    assert codex.declared_evidence(tmp_path) is not None


def test_agents_md_alone_is_not_evidence(tmp_path):
    """Instruction files are not configuration — Claude Code's own rule."""
    (tmp_path / "AGENTS.md").write_text("# instructions\n", encoding="utf-8")

    assert codex.declared_evidence(tmp_path) is None
    assert _declared(tmp_path) == []


def test_a_claude_plugin_manifest_alone_is_not_evidence(tmp_path):
    """Codex READS it as its second manifest candidate, but a tree carrying
    only Claude Code's manifest declares a Claude Code agent."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo"}), encoding="utf-8"
    )

    assert codex.declared_evidence(tmp_path) is None


def test_a_codex_surface_bundled_in_a_realized_plugin_is_not_evidence(tmp_path):
    """Otherwise a plugin's own fixture content trips a phantom Codex BOM."""
    (tmp_path / ".codex-plugin").mkdir()
    (tmp_path / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "outer"}), encoding="utf-8"
    )
    fixture = tmp_path / "examples" / ".codex"
    fixture.mkdir(parents=True)
    fixture.joinpath("hooks.json").write_text(json.dumps(HOOKS), encoding="utf-8")

    evidence = codex.declared_evidence(tmp_path)

    assert evidence is not None, "the outer plugin manifest is itself evidence"
    assert "examples" not in str(evidence)


def test_config_dir_overrides_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "env"))

    assert codex.resolve_config_root(tmp_path / "flag") == tmp_path / "flag"


def test_codex_home_is_honoured(tmp_path, monkeypatch):
    """ADR-0056: Codex declares a genuinely relocatable root."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "env"))

    assert codex.resolve_config_root() == tmp_path / "env"


def test_a_tilde_root_is_expanded(tmp_path, monkeypatch):
    """`~` reaches this function unexpanded whenever no shell expanded it —
    a Docker `ENV`, an MDM profile, a config file — and `Path("~/.codex")`
    names a directory called `~`, so discovery would silently find no agent.
    Matches `claude_code.resolve_config_root`, which expands both inputs.
    """
    monkeypatch.setenv("CODEX_HOME", "~/.codex")

    assert codex.resolve_config_root() == Path.home() / ".codex"
    assert codex.resolve_config_root(Path("~/elsewhere")) == Path.home() / "elsewhere"


def test_the_default_root_is_home_dot_codex(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)

    assert codex.resolve_config_root() == Path.home() / ".codex"


def test_config_dir_is_accepted_not_refused():
    """The counterpart to Cursor's refusal (ADR-0054 vs ADR-0056)."""
    assert codex.KIND.root_override_refusal is None


def test_an_empty_installed_root_still_yields_an_agent(tmp_path, monkeypatch):
    """An installed runtime with no configuration is a real agent with zero
    components — the deliberate asymmetry with `declared`."""
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    agents = codex.discover(DiscoveryContext(source="installed"))

    assert len(agents) == 1
    assert agents[0].kind_id == "codex"


def test_a_missing_installed_root_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))

    assert codex.discover(DiscoveryContext(source="installed")) == []


def test_coverage_baseline_is_complete_at_both_sources():
    """Under the rule — a gap lowers composition coverage only if it can hide a
    COMPONENT — nothing about a Codex scan is undetectable.

    Profile files genuinely did hide MCP servers and are now composed, which is
    what closed `installed`. The other candidates fail the rule rather than the
    evidence: `.rules` and `[projects.*]` declare no components, marketplace
    gaps cost identity rather than enumeration, and runtime MCP registration
    has zero references in the audited binary.
    """
    assert codex.COVERAGE_BASELINE == {"installed": "complete", "declared": "complete"}


def test_posture_rules_exclude_mcp_auto_approve_and_endpoint_override():
    from tools.posture.rules import (
        api_endpoint_override,
        command_policy_allow,
        mcp_auto_approve,
        project_trust,
    )

    rules = codex.KIND.posture_rules

    assert rules is not None
    assert mcp_auto_approve.RULE_ID not in rules
    assert api_endpoint_override.RULE_ID not in rules
    assert command_policy_allow.RULE_ID in rules
    assert project_trust.RULE_ID in rules


def test_three_kinds_are_registered_and_the_others_are_unchanged():
    from tools.agent_kinds import REGISTRY
    from tools.parsers import (
        CURSOR_MANIFEST_REGISTRY,
        HOST_AGNOSTIC_REGISTRY,
    )
    from tools.parsers import (
        REGISTRY as FLAT_REGISTRY,
    )

    by_id = {k.id: k for k in REGISTRY}

    assert set(by_id) == {"claude-code", "cursor", "codex"}
    assert by_id["claude-code"].manifest_patterns == tuple(FLAT_REGISTRY)
    assert by_id["cursor"].manifest_patterns == tuple(HOST_AGNOSTIC_REGISTRY) + tuple(
        CURSOR_MANIFEST_REGISTRY
    )


# --- The shared `.agents/` convention directory (ADR-0058) ------------------


def test_agents_skills_is_evidence_of_a_codex_agent(tmp_path):
    """ADR-0052 made this Cursor's exclusive evidence on the grounds that
    Cursor was the only reader. Codex reads it too, so it is evidence for both
    (ADR-0058)."""
    (tmp_path / ".agents" / "skills" / "shared").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "shared" / "SKILL.md").write_text(
        "---\nname: shared\n---\nS\n", encoding="utf-8"
    )

    assert codex.declared_evidence(tmp_path) is not None


def test_a_shared_skills_repo_declares_both_kinds(tmp_path):
    """The consequence ADR-0058 accepts: two BOMs for one tree, because two
    runtimes genuinely load those skills."""
    from tools.agent_kinds import cursor

    (tmp_path / ".agents" / "skills" / "shared").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "shared" / "SKILL.md").write_text(
        "---\nname: shared\n---\nS\n", encoding="utf-8"
    )

    assert codex.declared_evidence(tmp_path) is not None
    assert cursor.declared_evidence(tmp_path) is not None
