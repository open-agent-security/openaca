"""Plan 008 Task 6: repo-mode component inventory.

The same parsers that fire in fs mode also fire in repo mode via the
manifest registry, so a repo declaring `.claude/skills/`, `.claude/commands/`,
or `.claude/agents/` produces inventory components. Repo declarations are
not "via a plugin"; they're declared by the repo itself, so attribution
is None.
"""

from pathlib import Path

from tools.parsers import parse_repo

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_repo_mode_emits_declared_skill():
    refs = parse_repo(REPOS / "declared-components")
    skill_refs = [r for r in refs if r.ecosystem == "claude-skill"]
    assert len(skill_refs) == 1
    assert skill_refs[0].name == "bootstrap"
    assert skill_refs[0].version == "1.0.0"
    assert skill_refs[0].component_identity == "claude-skill/bootstrap@1.0.0"
    # Repo declarations are not attributed to any plugin.
    assert skill_refs[0].attributed_to is None


def test_repo_mode_emits_declared_command():
    refs = parse_repo(REPOS / "declared-components")
    cmd_refs = [r for r in refs if r.ecosystem == "claude-command"]
    assert len(cmd_refs) == 1
    assert cmd_refs[0].component_identity == "claude-command/repo/deploy"
    assert cmd_refs[0].attributed_to is None


def test_repo_mode_emits_declared_agent_with_frontmatter_name_override():
    """The fixture's agent file is named `reviewer.md` but the frontmatter
    declares `name: code-reviewer`. Identity should use the declared name."""
    refs = parse_repo(REPOS / "declared-components")
    agent_refs = [r for r in refs if r.ecosystem == "claude-agent"]
    assert len(agent_refs) == 1
    assert agent_refs[0].component_identity == "claude-agent/repo/code-reviewer"
    assert agent_refs[0].attributed_to is None
