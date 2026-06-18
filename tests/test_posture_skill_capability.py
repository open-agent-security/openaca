from pathlib import Path

from tools.parsers.claude_skill import parse
from tools.posture.rules.skill_capability import check_skill_executable_tools


def _write_skill(tmp_path: Path, name: str, allowed_tools: str) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"name: {name}\n"
        "description: Helps with local work\n"
        f"allowed-tools: {allowed_tools}\n"
        "---\n"
        "Run the requested workflow.\n",
        encoding="utf-8",
    )
    return skill_md


def test_skill_capability_detects_filtered_bash_tool(tmp_path: Path) -> None:
    """Bash(git:*) uses Claude's command-filter syntax; the base name must still match."""
    refs = parse(_write_skill(tmp_path, "git-helper", "Read, Bash(git:*)"))

    findings = check_skill_executable_tools(refs)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "openaca-posture-skill-executable-tool"
    assert finding.evidence["allowed_tools"] == ["Bash(git:*)"]


def test_skill_capability_detects_space_separated_allowed_tools(tmp_path: Path) -> None:
    """agentskills.io spec defines allowed-tools as space-separated, not comma-separated."""
    refs = parse(_write_skill(tmp_path, "space-helper", "Read Bash(git:*)"))

    findings = check_skill_executable_tools(refs)

    assert len(findings) == 1
    assert findings[0].evidence["allowed_tools"] == ["Bash(git:*)"]


def test_skill_capability_detects_space_inside_parens_allowed_tools(tmp_path: Path) -> None:
    """Bash(git add *) has spaces inside parens; it must remain one token."""
    refs = parse(
        _write_skill(
            tmp_path,
            "git-commit-helper",
            "Bash(git add *) Bash(git commit *) Read",
        )
    )

    findings = check_skill_executable_tools(refs)

    assert len(findings) == 1
    assert findings[0].evidence["allowed_tools"] == [
        "Bash(git add *)",
        "Bash(git commit *)",
    ]


def test_skill_capability_reports_posture_metadata(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, "deploy-helper", "Read, Bash")
    refs = parse(skill_md)

    findings = check_skill_executable_tools(refs)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.source == "openaca"
    assert finding.finding_type == "posture"
    assert finding.rule_id == "openaca-posture-skill-executable-tool"
    assert finding.severity == "low"
    assert finding.confidence == "high"
    assert finding.component == {
        "identity": "skill/deploy-helper",
        "name": "deploy-helper",
        "type": "skill",
    }
    assert finding.evidence["allowed_tools"] == ["Bash"]
    assert finding.declared_by == {"kind": "manifest", "path": str(skill_md)}
