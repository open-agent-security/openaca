"""The Pi kind (ADR-0081, docs/specs/pi-agent-kind.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.agent_kinds import DiscoveryContext, pi


def _write(path: Path, content: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _skill(directory: Path) -> Path:
    return _write(
        directory / "SKILL.md", f"---\nname: {directory.name}\ndescription: Do it.\n---\nS\n"
    )


def _declared(scan_root: Path):
    return pi.discover(DiscoveryContext(source="declared", scan_root=scan_root))


# --- Declared evidence ---------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        ".pi/settings.json",
        ".pi/mcp.json",
        ".pi/extensions/gate.ts",
        ".pi/extensions/gate.js",
        ".pi/extensions/tool/index.ts",
        ".pi/extensions/tool/package.json",
        ".pi/skills/deploy/SKILL.md",
        ".pi/skills/group/deploy/SKILL.md",
        ".pi/prompts/review.md",
        ".agents/skills/shared/SKILL.md",
        ".agents/skills/group/shared/SKILL.md",
        "packages/app/.pi/mcp.json",
    ],
)
def test_pi_owned_files_that_load_are_evidence(tmp_path, rel):
    _write(tmp_path / rel, "{}")

    assert pi.declared_evidence(tmp_path) is not None
    assert len(_declared(tmp_path)) == 1


@pytest.mark.parametrize(
    "rel",
    [
        ".pi/SYSTEM.md",
        ".pi/APPEND_SYSTEM.md",
        ".pi/themes/dark.json",
        ".pi/prompts/nested/review.md",
        ".pi/extensions/tool/src/index.ts",
        ".pi/extensions/notes.txt",
        ".pi/skills/.hidden/deploy/SKILL.md",
        ".pi/skills/node_modules/deploy/SKILL.md",
        ".pi/npm/node_modules/tools/package.json",
        "extensions/gate.ts",
        "skills/deploy/SKILL.md",
        "AGENTS.md",
    ],
)
def test_other_files_are_not_evidence(tmp_path, rel):
    _write(tmp_path / rel, "{}")

    assert pi.declared_evidence(tmp_path) is None
    assert _declared(tmp_path) == []


def test_a_pi_package_repository_is_not_evidence(tmp_path):
    """The spec defers a repository that is itself a Pi package."""
    _write(tmp_path / "package.json", json.dumps({"name": "tools", "pi": {"skills": ["./s"]}}))
    _skill(tmp_path / "s" / "review")

    assert _declared(tmp_path) == []


def test_gitignored_evidence_is_not_evidence(tmp_path):
    _write(tmp_path / ".gitignore", ".pi/\n")
    _write(tmp_path / ".pi" / "mcp.json", "{}")

    assert _declared(tmp_path) == []
    assert (
        len(
            pi.discover(
                DiscoveryContext(source="declared", scan_root=tmp_path, include_gitignored=True)
            )
        )
        == 1
    )


def test_a_pi_only_repo_declares_only_pi(tmp_path):
    from tools.agent_kinds import discover_agents

    _write(tmp_path / ".pi" / "mcp.json", "{}")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert [a.kind_id for a in agents] == ["pi"]


# --- Installed discovery -----------------------------------------------------------


def test_the_agent_directory_defaults_under_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert pi.resolve_config_root() == tmp_path / ".pi" / "agent"


def test_pi_coding_agent_dir_moves_the_agent_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "~/elsewhere/agent")
    (tmp_path / "elsewhere" / "agent").mkdir(parents=True)

    (agent,) = pi.discover(DiscoveryContext(source="installed"))

    assert agent.config_root == tmp_path / "elsewhere" / "agent"


def test_config_dir_is_refused(tmp_path):
    from tools.kind_selection import KindSelectionError, validate_kind_selection

    with pytest.raises(KindSelectionError, match="PI_CODING_AGENT_DIR"):
        validate_kind_selection("pi", tmp_path)


def test_an_empty_installed_root_still_yields_an_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".pi" / "agent").mkdir(parents=True)

    (agent,) = pi.discover(DiscoveryContext(source="installed"))

    assert agent.kind_id == "pi"
    assert agent.bom_ref == "root/pi"
    assert agent.coverage_baseline == "partial"


def test_a_missing_installed_root_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".pi").mkdir()

    assert pi.discover(DiscoveryContext(source="installed")) == []


# --- Registration ----------------------------------------------------------------


def test_coverage_baseline_is_partial_at_both_sources():
    assert pi.COVERAGE_BASELINE == {"installed": "partial", "declared": "partial"}


def test_the_kind_is_a_registered_singleton():
    from tools.agent_kinds import REGISTRY, kind_for

    assert kind_for("pi") is pi.KIND
    assert pi.KIND in REGISTRY
    assert pi.KIND.cardinality == "singleton"
    assert pi.KIND.display_name == "Pi"


def test_every_rule_whose_input_pi_has_is_allowlisted():
    from tools.posture import KNOWN_RULE_IDS
    from tools.posture.rules import api_endpoint_override, command_policy_allow, mcp_auto_approve

    assert pi.KIND.posture_rules == KNOWN_RULE_IDS - {
        mcp_auto_approve.RULE_ID,
        command_policy_allow.RULE_ID,
        api_endpoint_override.RULE_ID,
    }


def test_pi_lists_no_builtins(tmp_path, monkeypatch):
    """Pi has no built-in sub-agents or skills (ADR-0076)."""
    from tools.agent_kinds import Builtins, builtins_of

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    (agent,) = pi.discover(DiscoveryContext(source="installed"))

    assert builtins_of(agent) == Builtins()


def test_compose_reads_home_for_the_shared_skills(tmp_path, monkeypatch):
    """The kind resolves home; the builder never reads the environment."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    _skill(tmp_path / ".agents" / "skills" / "shared")
    (agent,) = pi.discover(DiscoveryContext(source="installed"))

    graph = pi.KIND.compose(agent)

    assert [n.ref.name for n in graph.nodes.values() if n.kind == "skill" and n.ref] == ["shared"]


# --- The manifest registry agrees with composition ----------------------------------


def _counts(scan_root: Path) -> tuple[int, int]:
    from tools.parsers import parse_repo_grouped

    grouped, found = parse_repo_grouped(
        scan_root, registry=pi.KIND.manifest_patterns, surface=pi.KIND.repo_surface
    )
    return found, found - len(grouped)


def test_a_malformed_pi_file_is_a_failed_source_unit(tmp_path):
    _write(tmp_path / ".pi" / "mcp.json", "{not json")
    _write(tmp_path / ".pi" / "settings.json", "[]")

    assert _counts(tmp_path) == (2, 2)


def test_nested_skills_pi_does_not_load_are_not_source_units(tmp_path):
    _skill(tmp_path / ".pi" / "skills" / "outer")
    _skill(tmp_path / ".pi" / "skills" / "outer" / "inner")
    _skill(tmp_path / ".pi" / "skills" / ".hidden" / "skipped")
    _skill(tmp_path / ".agents" / "skills" / "group" / "shared")

    assert _counts(tmp_path) == (2, 0)


def test_each_pi_surface_is_a_source_unit(tmp_path):
    _write(tmp_path / ".pi" / "mcp.json", json.dumps({"mcpServers": {}}))
    _write(tmp_path / ".pi" / "settings.json", "{}")
    _write(tmp_path / ".pi" / "prompts" / "review.md", "Review.\n")
    _write(tmp_path / ".pi" / "extensions" / "gate.ts", "")
    _write(tmp_path / ".pi" / "extensions" / "tool" / "index.js", "")

    assert _counts(tmp_path) == (5, 0)
