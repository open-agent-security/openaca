"""Tests for claude_command_agent parser (claude-command + claude-agent).

Slash commands and subagents are markdown files under a directory.
Identity = filename basename (without `.md`). Optional YAML frontmatter
may override the name via a `name:` field.

Identity is name-based; repo/plugin location is carried separately.
"""

from pathlib import Path

from tools.parsers.claude_command_agent import enumerate_dir


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_enumerate_emits_one_ref_per_markdown_file(tmp_path):
    _write(tmp_path / "commands" / "foo.md", "Just markdown, no frontmatter.\n")
    _write(tmp_path / "commands" / "bar.md", "More markdown.\n")
    refs = enumerate_dir(
        tmp_path / "commands",
        kind="command",
        scope_owner="superpowers",
        attributed_to="claude-plugin/superpowers@5.1.0",
    )
    assert len(refs) == 2
    identities = sorted(r.component_identity or "" for r in refs)
    assert identities == [
        "claude-command/superpowers/bar",
        "claude-command/superpowers/foo",
    ]
    assert all(r.ecosystem == "claude-command" for r in refs)
    assert all(r.attributed_to == "claude-plugin/superpowers@5.1.0" for r in refs)
    assert all(r.extra["scope_owner"] == "superpowers" for r in refs)


def test_enumerate_uses_filename_when_no_frontmatter(tmp_path):
    path = _write(tmp_path / "agents" / "reviewer.md", "Plain markdown.\n")
    refs = enumerate_dir(
        tmp_path / "agents",
        kind="agent",
        scope_owner="repo",
        attributed_to=None,
    )
    assert len(refs) == 1
    assert refs[0].name == "reviewer"
    assert refs[0].component_identity == "claude-agent/reviewer"
    assert refs[0].source_manifest == str(path)
    assert refs[0].attributed_to is None
    assert refs[0].extra["scope_owner"] == "repo"


def test_enumerate_frontmatter_name_overrides_filename(tmp_path):
    """If frontmatter declares `name:`, prefer it over the filename basename."""
    _write(
        tmp_path / "commands" / "filename-default.md",
        "---\nname: declared-cmd\n---\nbody\n",
    )
    refs = enumerate_dir(
        tmp_path / "commands",
        kind="command",
        scope_owner="repo",
        attributed_to=None,
    )
    assert len(refs) == 1
    assert refs[0].name == "declared-cmd"
    assert refs[0].component_identity == "claude-command/declared-cmd"


def test_enumerate_invalid_frontmatter_falls_back_to_filename(tmp_path):
    """Malformed YAML in frontmatter should not break enumeration."""
    _write(
        tmp_path / "agents" / "robust.md",
        "---\nname: x\nfield: ]\n---\nbody\n",
    )
    refs = enumerate_dir(
        tmp_path / "agents",
        kind="agent",
        scope_owner="repo",
        attributed_to=None,
    )
    assert len(refs) == 1
    assert refs[0].name == "robust"


def test_enumerate_skips_non_markdown_files(tmp_path):
    _write(tmp_path / "commands" / "README.txt", "not a command\n")
    _write(tmp_path / "commands" / "real.md", "yes\n")
    refs = enumerate_dir(
        tmp_path / "commands",
        kind="command",
        scope_owner="repo",
        attributed_to=None,
    )
    assert len(refs) == 1
    assert refs[0].name == "real"


def test_enumerate_returns_empty_for_missing_dir(tmp_path):
    """Walking a directory that doesn't exist is not an error — return []."""
    refs = enumerate_dir(
        tmp_path / "no-such-dir",
        kind="command",
        scope_owner="repo",
        attributed_to=None,
    )
    assert refs == []


def test_enumerate_returns_empty_for_empty_dir(tmp_path):
    (tmp_path / "empty").mkdir()
    refs = enumerate_dir(
        tmp_path / "empty",
        kind="agent",
        scope_owner="repo",
        attributed_to=None,
    )
    assert refs == []


def test_enumerate_skips_subdirectories_with_md_suffix(tmp_path):
    """A directory named foo.md should not be treated as a command file."""
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "weird.md").mkdir()
    refs = enumerate_dir(
        tmp_path / "commands",
        kind="command",
        scope_owner="repo",
        attributed_to=None,
    )
    assert refs == []


def test_enumerate_propagates_scope_owner_for_plugin_bundled_agents(tmp_path):
    _write(tmp_path / "agents" / "code-reviewer.md", "body\n")
    refs = enumerate_dir(
        tmp_path / "agents",
        kind="agent",
        scope_owner="superpowers",
        attributed_to="claude-plugin/superpowers@5.1.0",
    )
    assert len(refs) == 1
    assert refs[0].component_identity == "claude-agent/superpowers/code-reviewer"
    assert refs[0].attributed_to == "claude-plugin/superpowers@5.1.0"


def test_enumerate_frontmatter_without_name_field_falls_back_to_filename(tmp_path):
    """Frontmatter exists but no name key → use filename."""
    _write(
        tmp_path / "commands" / "fallback.md",
        "---\ndescription: a description but no name\n---\nbody\n",
    )
    refs = enumerate_dir(
        tmp_path / "commands",
        kind="command",
        scope_owner="repo",
        attributed_to=None,
    )
    assert len(refs) == 1
    assert refs[0].name == "fallback"
