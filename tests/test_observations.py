from pathlib import Path

from tools.observations import collect_skill_observations
from tools.parsers.claude_skill import parse


def test_skill_audit_detects_filtered_bash_tool(tmp_path: Path) -> None:
    """Bash(git:*) uses Claude's command-filter syntax; the base name must still match."""
    skill_dir = tmp_path / "git-helper"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: git-helper\n"
        "description: Helps with git operations\n"
        "allowed-tools: Read, Bash(git:*)\n"
        "---\n"
        "Run git operations.\n"
    )
    refs = parse(skill_md)

    observations = collect_skill_observations(refs)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.observation_id == "skill.allowed-executable-tool"
    assert observation.evidence["allowed_tools"] == ["Bash(git:*)"]


def test_skill_audit_detects_space_separated_allowed_tools(tmp_path: Path) -> None:
    """agentskills.io spec defines allowed-tools as space-separated, not comma-separated."""
    skill_dir = tmp_path / "space-helper"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: space-helper\n"
        "description: Uses space-separated allowed-tools per agentskills.io spec\n"
        "allowed-tools: Read Bash(git:*)\n"
        "---\n"
        "Run git operations.\n"
    )
    refs = parse(skill_md)

    observations = collect_skill_observations(refs)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.observation_id == "skill.allowed-executable-tool"
    assert observation.evidence["allowed_tools"] == ["Bash(git:*)"]


def test_skill_audit_detects_space_inside_parens_allowed_tools(tmp_path: Path) -> None:
    """Bash(git add *) has spaces inside parens; it must remain one token."""
    skill_dir = tmp_path / "git-commit-helper"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: git-commit-helper\n"
        "description: Stages and commits files\n"
        "allowed-tools: Bash(git add *) Bash(git commit *) Read\n"
        "---\n"
        "Stage and commit.\n"
    )
    refs = parse(skill_md)

    observations = collect_skill_observations(refs)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.observation_id == "skill.allowed-executable-tool"
    assert observation.evidence["allowed_tools"] == ["Bash(git add *)", "Bash(git commit *)"]


def test_skill_audit_observes_executable_allowed_tools(tmp_path: Path) -> None:
    skill_dir = tmp_path / "deploy-helper"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: deploy-helper\n"
        "description: Helps deploy services\n"
        "allowed-tools: Read, Bash\n"
        "---\n"
        "Run the deploy checklist.\n"
    )
    refs = parse(skill_md)

    observations = collect_skill_observations(refs)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.source == "openaca-skill-audit"
    assert observation.observation_id == "skill.allowed-executable-tool"
    assert observation.severity == "low"
    assert observation.confidence == "high"
    assert observation.component == {
        "identity": "skill/deploy-helper",
        "name": "deploy-helper",
        "type": "skill",
    }
    assert observation.subject_coordinate.startswith("sha256:")
    assert observation.evidence["allowed_tools"] == ["Bash"]
    assert observation.declared_by == {"kind": "manifest", "path": str(skill_md)}
