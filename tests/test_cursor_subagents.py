"""Tests for the first-wins subagent precedence resolver.

See `tools/cursor_subagents.py` for the rule: `.cursor` beats `.claude`
within a scope, and scope precedence outranks that directory order — a
project (workspace) scope's subagents entirely displace a personal (user)
scope's, unconditionally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.cursor_subagents import resolve_endpoint, resolve_repo


def _write(path: Path, content: str = "content\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_cursor_overrides_claude_within_same_scope(tmp_path):
    _write(tmp_path / ".cursor" / "agents" / "deploy.md", "cursor version\n")
    _write(tmp_path / ".claude" / "agents" / "deploy.md", "claude version\n")

    resolved = resolve_repo(tmp_path)

    assert len(resolved) == 1
    assert resolved[0].relative_path == "deploy.md"
    assert resolved[0].agents_dir == tmp_path / ".cursor" / "agents"


def test_no_override_both_included(tmp_path):
    _write(tmp_path / ".cursor" / "agents" / "deploy.md")
    _write(tmp_path / ".claude" / "agents" / "review.md")

    resolved = resolve_repo(tmp_path)

    relative_paths = sorted(r.relative_path for r in resolved)
    assert relative_paths == ["deploy.md", "review.md"]


def test_project_scope_beats_user_scope_regardless_of_directory(tmp_path):
    project_dir = tmp_path / "project"
    user_dir = tmp_path / "home" / ".cursor"
    project_claude_agents = _write(
        project_dir / ".claude" / "agents" / "foo.md", "project .claude\n"
    ).parent
    user_cursor_agents = _write(user_dir / "agents" / "foo.md", "user .cursor\n").parent

    resolved = resolve_endpoint([project_claude_agents, user_cursor_agents])

    assert len(resolved) == 1
    assert resolved[0].agents_dir == project_claude_agents
    assert resolved[0].file_path.read_text() == "project .claude\n"


def test_endpoint_mode_never_reconstructs_root_from_basename(tmp_path):
    arbitrary = tmp_path / "somewhere" / "not-named-agents-parent" / "agents"
    _write(arbitrary / "foo.md")

    resolved = resolve_endpoint([arbitrary])

    assert len(resolved) == 1
    assert resolved[0].file_path == arbitrary / "foo.md"


def test_symlinked_agents_dir_escaping_root_is_dropped(tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "agents" / "evil.md")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".cursor").mkdir()
    try:
        (repo / ".cursor" / "agents").symlink_to(outside / "agents", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported on this platform")

    resolved = resolve_repo(repo)

    assert resolved == []


def test_nested_md_symlink_escaping_its_dir_is_dropped(tmp_path):
    outside_file = _write(tmp_path / "outside.md", "secret\n")
    repo = tmp_path / "repo"
    agents_dir = repo / ".cursor" / "agents"
    agents_dir.mkdir(parents=True)
    _write(agents_dir / "safe.md")
    try:
        (agents_dir / "escape.md").symlink_to(outside_file)
    except OSError:
        pytest.skip("symlinks unsupported on this platform")

    resolved = resolve_repo(repo)

    relative_paths = sorted(r.relative_path for r in resolved)
    assert relative_paths == ["safe.md"]


def test_malformed_file_isolated_to_one_subagent(tmp_path, monkeypatch):
    _write(tmp_path / ".cursor" / "agents" / "good.md", "fine\n")
    bad = _write(tmp_path / ".cursor" / "agents" / "bad.md", "unparseable\n")

    import tools.cursor_subagents as cursor_subagents
    from tools.parsers import claude_command_agent

    real_parse_file = claude_command_agent.parse_file

    def _flaky_parse_file(path, kind, scope_owner=None, **kwargs):
        if path == bad:
            raise ValueError("boom")
        return real_parse_file(path, kind=kind, scope_owner=scope_owner, **kwargs)

    monkeypatch.setattr(cursor_subagents.claude_command_agent, "parse_file", _flaky_parse_file)

    resolved = resolve_repo(tmp_path)

    relative_paths = sorted(r.relative_path for r in resolved)
    assert relative_paths == ["bad.md", "good.md"]
    bad_entry = next(r for r in resolved if r.relative_path == "bad.md")
    assert bad_entry.refs == ()
    good_entry = next(r for r in resolved if r.relative_path == "good.md")
    assert len(good_entry.refs) == 1


def test_repo_mode_groups_by_scope_dir_parent_parent(tmp_path):
    _write(tmp_path / "sub" / ".cursor" / "agents" / "one.md")
    _write(tmp_path / "other" / ".claude" / "agents" / "two.md")

    resolved = resolve_repo(tmp_path)

    agents_dirs = sorted(str(r.agents_dir) for r in resolved)
    assert agents_dirs == sorted(
        [
            str(tmp_path / "sub" / ".cursor" / "agents"),
            str(tmp_path / "other" / ".claude" / "agents"),
        ]
    )


def test_ignored_higher_precedence_file_does_not_shadow_lower_precedence_winner(tmp_path):
    """A gitignored `.cursor/agents/foo.md` must not win first-wins
    resolution over an unignored `.claude/agents/foo.md` at the same
    relative path: filtering only the resolution winner (post-hoc) would
    drop the ignored `.cursor` entry AND lose the `.claude` entry it had
    already excluded via `relative_path in seen`, dropping the subagent
    entirely."""
    ignored = _write(tmp_path / ".cursor" / "agents" / "foo.md", "cursor version\n")
    _write(tmp_path / ".claude" / "agents" / "foo.md", "claude version\n")

    resolved = resolve_repo(tmp_path, is_ignored=lambda p: p == ignored)

    assert len(resolved) == 1
    assert resolved[0].relative_path == "foo.md"
    assert resolved[0].agents_dir == tmp_path / ".claude" / "agents"


def test_traversal_depth_10_included_depth_11_excluded(tmp_path):
    agents_dir = tmp_path / ".cursor" / "agents"
    depth_10 = agents_dir.joinpath(*["d"] * 9, "at-depth-10.md")
    depth_11 = agents_dir.joinpath(*["d"] * 10, "at-depth-11.md")
    _write(depth_10)
    _write(depth_11)

    resolved = resolve_endpoint([agents_dir])

    relative_paths = {r.relative_path for r in resolved}
    assert depth_10.relative_to(agents_dir).as_posix() in relative_paths
    assert depth_11.relative_to(agents_dir).as_posix() not in relative_paths
