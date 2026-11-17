"""The OpenCode kind (ADR-0083, docs/specs/opencode-agent-kind.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.agent_kinds import DiscoveryContext, builtins_of, discover_agents, kind_for, opencode
from tools.kind_selection import KindSelectionError, validate_kind_selection
from tools.posture.rules import insecure_transport, mcp_header_credential, mutable_install


def _write(path: Path, content: str = "{}\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _skill(directory: Path, name: str = "s") -> Path:
    return _write(directory / "SKILL.md", f"---\nname: {name}\n---\nS\n")


def _declared(scan_root: Path):
    return opencode.discover(DiscoveryContext(source="declared", scan_root=scan_root))


# --- Declared evidence ---------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "opencode.json",
        "opencode.jsonc",
        ".opencode/opencode.json",
        ".opencode/agents/reviewer.md",
        ".opencode/agent/team/lead.md",
        ".opencode/modes/focus.md",
        ".opencode/commands/ship.md",
        ".opencode/command/nested/deploy.md",
        ".opencode/plugins/notify.ts",
        ".opencode/plugin/notify.js",
        ".opencode/skills/release/SKILL.md",
        ".opencode/skill/group/release/SKILL.md",
        ".agents/skills/release/SKILL.md",
        ".agents/skills/group/release/SKILL.md",
        "packages/app/opencode.json",
        "packages/app/.opencode/agents/reviewer.md",
    ],
)
def test_opencode_owned_files_are_evidence(tmp_path, rel):
    _write(tmp_path / rel)

    assert opencode.declared_evidence(tmp_path) is not None
    assert len(_declared(tmp_path)) == 1


@pytest.mark.parametrize(
    "rel",
    [
        ".claude/skills/release/SKILL.md",
        ".opencode/tools/lint.ts",
        ".opencode/agents/notes.txt",
        ".opencode/modes/sub/focus.md",
        ".opencode/plugins/sub/notify.ts",
        ".opencode/plugins/notify.py",
        ".opencode/skills/.hidden/release/SKILL.md",
        ".opencode/package.json",
        ".opencode/node_modules/pkg/README.md",
        "AGENTS.md",
        "tui.json",
    ],
)
def test_files_opencode_does_not_compose_or_does_not_own_are_not_evidence(tmp_path, rel):
    """Claude Code's skills are composition only, a custom tool is not
    composed, and a file outside OpenCode's globs loads nothing."""
    _write(tmp_path / rel)

    assert opencode.declared_evidence(tmp_path) is None
    assert _declared(tmp_path) == []


def test_a_gitignored_config_is_not_evidence(tmp_path):
    _write(tmp_path / ".gitignore", "opencode.json\n")
    _write(tmp_path / "opencode.json")

    assert opencode.declared_evidence(tmp_path) is None
    assert opencode.declared_evidence(tmp_path, include_gitignored=True) is not None


def test_a_shared_skills_repo_declares_opencode_too(tmp_path):
    """ADR-0058's stated cost, applied to a fourth reader."""
    _skill(tmp_path / ".agents" / "skills" / "shared")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert "opencode" in {a.kind_id for a in agents}


def test_a_claude_skills_repo_declares_no_opencode_agent(tmp_path):
    _skill(tmp_path / ".claude" / "skills" / "only-claude")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert "opencode" not in {a.kind_id for a in agents}


# --- Installed discovery ---------------------------------------------------------


def test_the_config_root_existing_is_the_installed_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert opencode.discover(DiscoveryContext(source="installed")) == []

    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    (agent,) = opencode.discover(DiscoveryContext(source="installed", project_root=tmp_path))

    assert agent.config_root == tmp_path / ".config" / "opencode"
    assert agent.project_root == tmp_path
    assert agent.coverage_baseline == "partial"
    assert agent.bom_ref == "root/opencode"


def test_xdg_variables_move_the_config_root_and_the_plugin_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))

    assert opencode.resolve_config_root() == tmp_path / "xdg-config" / "opencode"
    assert opencode.resolve_cache_root() == tmp_path / "xdg-cache" / "opencode"


def test_a_relative_xdg_value_falls_back_to_the_default(tmp_path, monkeypatch):
    """OpenCode resolves it against its own working directory, which a scan
    cannot know."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/cache")

    assert opencode.resolve_config_root() == tmp_path / ".config" / "opencode"
    assert opencode.resolve_cache_root() == tmp_path / ".cache" / "opencode"


def test_config_dir_is_refused_with_opencodes_own_reason(tmp_path):
    with pytest.raises(KindSelectionError) as excinfo:
        validate_kind_selection("opencode", tmp_path)

    assert "--config-dir is not supported for --kind opencode" in str(excinfo.value)
    assert "plugin cache" in str(excinfo.value)


# --- The kind --------------------------------------------------------------------


def test_opencode_is_registered_with_only_the_rules_its_input_supports():
    """ADR-0085: no permission rule and no skill rule."""
    kind = kind_for("opencode")

    assert kind is opencode.KIND
    assert kind.cardinality == "singleton"
    assert kind.coverage_baseline == {"installed": "partial", "declared": "partial"}
    assert kind.posture_rules == {
        insecure_transport.RULE_ID,
        mcp_header_credential.RULE_ID,
        mutable_install.RULE_ID,
    }


def test_builtins_are_the_native_agents_and_the_built_in_skill(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".config" / "opencode").mkdir(parents=True)
    (installed,) = opencode.discover(DiscoveryContext(source="installed"))
    _write(tmp_path / "repo" / "opencode.json", json.dumps({}))
    (declared,) = _declared(tmp_path / "repo")

    builtins = builtins_of(installed)

    assert builtins.agents == (
        "build",
        "compaction",
        "explore",
        "general",
        "plan",
        "summary",
        "title",
    )
    assert builtins.skills == ("customize-opencode",)
    assert builtins_of(declared).agents == ()
