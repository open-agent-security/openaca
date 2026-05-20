import json

from tools.parsers import skill_lock


def test_parse_global_skill_lock_v3_normalizes_source_fields(tmp_path):
    lock = tmp_path / ".skill-lock.json"
    lock.write_text(
        json.dumps(
            {
                "version": 3,
                "skills": {
                    "aws-api": {
                        "source": "awslabs/agent-toolkit-for-aws",
                        "sourceType": "github",
                        "sourceUrl": "https://github.com/awslabs/agent-toolkit-for-aws.git",
                        "ref": "main",
                        "skillPath": "skills/aws-api/SKILL.md",
                        "skillFolderHash": "1234567890abcdef",
                        "pluginName": "agent-toolkit-for-aws",
                        "installedAt": "2026-05-01T00:00:00Z",
                        "updatedAt": "2026-05-02T00:00:00Z",
                    }
                },
            }
        )
    )

    entries = skill_lock.parse(lock)

    entry = entries["aws-api"]
    assert entry.name == "aws-api"
    assert entry.source == "awslabs/agent-toolkit-for-aws"
    assert entry.source_type == "github"
    assert entry.source_url == "https://github.com/awslabs/agent-toolkit-for-aws.git"
    assert entry.ref == "main"
    assert entry.skill_path == "skills/aws-api/SKILL.md"
    assert entry.hash == "1234567890abcdef"
    assert entry.hash_type == "skillFolderHash"
    assert entry.plugin_name == "agent-toolkit-for-aws"
    assert entry.lockfile_path == str(lock)


def test_parse_project_skill_lock_v1_uses_computed_hash(tmp_path):
    lock = tmp_path / "skills-lock.json"
    lock.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "bootstrap": {
                        "source": "vercel-labs/agent-skills",
                        "sourceType": "github",
                        "ref": "feature/bootstrap",
                        "skillPath": "skills/bootstrap/SKILL.md",
                        "computedHash": "abcdef1234567890",
                    }
                },
            }
        )
    )

    entries = skill_lock.parse(lock)

    entry = entries["bootstrap"]
    assert entry.source == "vercel-labs/agent-skills"
    assert entry.source_type == "github"
    assert entry.ref == "feature/bootstrap"
    assert entry.skill_path == "skills/bootstrap/SKILL.md"
    assert entry.hash == "abcdef1234567890"
    assert entry.hash_type == "computedHash"


def test_parse_ignores_malformed_lock_entries(tmp_path):
    lock = tmp_path / ".skill-lock.json"
    lock.write_text(
        json.dumps(
            {
                "version": 3,
                "skills": {
                    "missing-source-type": {"source": "org/repo"},
                    "not-object": "bad",
                    "good": {
                        "source": "org/repo",
                        "sourceType": "github",
                        "skillFolderHash": "abcd",
                    },
                },
            }
        )
    )

    entries = skill_lock.parse(lock)

    assert set(entries) == {"good"}


def test_parse_returns_empty_for_missing_or_invalid_lock(tmp_path):
    assert skill_lock.parse(tmp_path / "missing.json") == {}

    invalid = tmp_path / ".skill-lock.json"
    invalid.write_text("{not json")
    assert skill_lock.parse(invalid) == {}
