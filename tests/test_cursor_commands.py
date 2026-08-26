"""Tests for the last-wins command precedence resolver.

See `tools/cursor_commands.py` for the rule: `.cursor` overwrites `.claude`
within a scope, and a later scope unconditionally overwrites an earlier
one's same-relative-path entry — user (personal) scope is the eventual
winner. This is the opposite direction of `tools/cursor_subagents.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.cursor_commands import resolve_endpoint, resolve_repo


def _write(path: Path, content: str = "content\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_cursor_overrides_claude_within_same_scope(tmp_path):
    _write(tmp_path / ".claude" / "commands" / "deploy.md", "claude version\n")
    _write(tmp_path / ".cursor" / "commands" / "deploy.md", "cursor version\n")

    resolved = resolve_repo(tmp_path)

    assert len(resolved) == 1
    assert resolved[0].relative_path == "deploy.md"
    assert resolved[0].commands_dir == tmp_path / ".cursor" / "commands"


def test_last_wins_across_two_same_relative_path_scopes(tmp_path):
    workspace_dir = _write(
        tmp_path / "workspace" / ".claude" / "commands" / "deploy.md", "workspace\n"
    ).parent
    personal_dir = _write(
        tmp_path / "personal" / ".cursor" / "commands" / "deploy.md", "personal\n"
    ).parent

    resolved = resolve_endpoint([workspace_dir, personal_dir])

    assert len(resolved) == 1
    assert resolved[0].commands_dir == personal_dir
    assert resolved[0].file_path.read_text() == "personal\n"

    reversed_resolved = resolve_endpoint([personal_dir, workspace_dir])
    assert reversed_resolved[0].commands_dir == workspace_dir
    assert reversed_resolved[0].file_path.read_text() == "workspace\n"


def test_distinct_nested_relative_paths_from_different_scopes_coexist(tmp_path):
    team_dir = _write(tmp_path / "team" / "commands" / "alpha.md").parent
    personal_dir = _write(tmp_path / "personal" / ".cursor" / "commands" / "beta.md").parent

    resolved = resolve_endpoint([team_dir, personal_dir])

    relative_paths = sorted(r.relative_path for r in resolved)
    assert relative_paths == ["alpha.md", "beta.md"]


def test_endpoint_mode_never_reconstructs_root_from_basename(tmp_path):
    arbitrary = tmp_path / "somewhere" / "not-named-commands-parent" / "commands"
    _write(arbitrary / "foo.md")

    resolved = resolve_endpoint([arbitrary])

    assert len(resolved) == 1
    assert resolved[0].file_path == arbitrary / "foo.md"


def test_symlinked_commands_dir_escaping_root_is_dropped(tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "commands" / "evil.md")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".cursor").mkdir()
    try:
        (repo / ".cursor" / "commands").symlink_to(outside / "commands", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported on this platform")

    resolved = resolve_repo(repo)

    assert resolved == []


def test_nested_md_symlink_escaping_its_dir_is_dropped(tmp_path):
    outside_file = _write(tmp_path / "outside.md", "secret\n")
    repo = tmp_path / "repo"
    commands_dir = repo / ".cursor" / "commands"
    commands_dir.mkdir(parents=True)
    _write(commands_dir / "safe.md")
    try:
        (commands_dir / "escape.md").symlink_to(outside_file)
    except OSError:
        pytest.skip("symlinks unsupported on this platform")

    resolved = resolve_repo(repo)

    relative_paths = sorted(r.relative_path for r in resolved)
    assert relative_paths == ["safe.md"]


def test_malformed_file_isolated_to_one_command(tmp_path, monkeypatch):
    _write(tmp_path / ".cursor" / "commands" / "good.md", "fine\n")
    bad = _write(tmp_path / ".cursor" / "commands" / "bad.md", "unparseable\n")

    import tools.cursor_commands as cursor_commands
    from tools.parsers import claude_command_agent

    real_parse_file = claude_command_agent.parse_file

    def _flaky_parse_file(path, kind, scope_owner=None, **kwargs):
        if path == bad:
            raise ValueError("boom")
        return real_parse_file(path, kind=kind, scope_owner=scope_owner, **kwargs)

    monkeypatch.setattr(cursor_commands.claude_command_agent, "parse_file", _flaky_parse_file)

    resolved = resolve_repo(tmp_path)

    relative_paths = sorted(r.relative_path for r in resolved)
    assert relative_paths == ["bad.md", "good.md"]
    bad_entry = next(r for r in resolved if r.relative_path == "bad.md")
    assert bad_entry.refs == ()
    good_entry = next(r for r in resolved if r.relative_path == "good.md")
    assert len(good_entry.refs) == 1


def test_repo_mode_groups_by_scope_dir_parent_parent(tmp_path):
    _write(tmp_path / "sub" / ".cursor" / "commands" / "one.md")
    _write(tmp_path / "other" / ".claude" / "commands" / "two.md")

    resolved = resolve_repo(tmp_path)

    commands_dirs = sorted(str(r.commands_dir) for r in resolved)
    assert commands_dirs == sorted(
        [
            str(tmp_path / "sub" / ".cursor" / "commands"),
            str(tmp_path / "other" / ".claude" / "commands"),
        ]
    )


def test_traversal_depth_10_included_depth_11_excluded(tmp_path):
    commands_dir = tmp_path / ".cursor" / "commands"
    depth_10 = commands_dir.joinpath(*["d"] * 9, "at-depth-10.md")
    depth_11 = commands_dir.joinpath(*["d"] * 10, "at-depth-11.md")
    _write(depth_10)
    _write(depth_11)

    resolved = resolve_endpoint([commands_dir])

    relative_paths = {r.relative_path for r in resolved}
    assert depth_10.relative_to(commands_dir).as_posix() in relative_paths
    assert depth_11.relative_to(commands_dir).as_posix() not in relative_paths
