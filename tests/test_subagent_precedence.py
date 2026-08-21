import os

from tools.parsers import claude_command_agent
from tools.subagent_precedence import resolve_subagent_occurrences


def test_symlinked_agents_dir_outside_root_is_rejected(tmp_path):
    # `.claude/agents` itself is a symlink pointing outside the scan root —
    # every other repo-mode manifest walk (`iter_unignored_files`'s
    # `os.walk(followlinks=False)`) would never descend into this, so
    # `_discover_subagent_scopes` must reject it the same way
    # `claude_plugin_root`'s `resolve_within` rejects a plugin's symlinked
    # default `agents/` dir.
    external_agents = tmp_path / "external_agents"
    external_agents.mkdir()
    (external_agents / "evil.md").write_text("---\nname: evil\n---\nEscaped agent.\n")

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    os.symlink(external_agents, repo / ".claude" / "agents")

    refs = resolve_subagent_occurrences(repo, hosts=["claude-code", "cursor"])
    assert refs == []


def test_symlinked_md_inside_agents_dir_outside_root_is_rejected(tmp_path):
    # A legitimate `.claude/agents` dir containing a *.md that is itself a
    # symlink escaping the scan root must have that file dropped, mirroring
    # `claude_command_agent.enumerate_dir`'s `contain_within` guard for
    # plugin-bundled agents/.
    external_content = tmp_path / "external.md"
    external_content.write_text("---\nname: evil\n---\nEscaped agent.\n")

    repo = tmp_path / "repo"
    claude_agents = repo / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "healthy.md").write_text("---\nname: healthy\n---\ny\n")
    os.symlink(external_content, claude_agents / "evil.md")

    refs = resolve_subagent_occurrences(repo, hosts=["claude-code", "cursor"])
    assert len(refs) == 1
    assert refs[0].source_manifest.endswith("healthy.md")


def test_malformed_subagent_file_does_not_abort_other_subagents(tmp_path, monkeypatch):
    # One malformed .md (parse_file raises) must cost only that one file —
    # every other subagent in the scan must still resolve. Mirrors
    # graph_build._safe_parse's per-manifest isolation contract.
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "broken.md").write_text("---\nname: broken\n---\nx\n")
    (claude_agents / "healthy.md").write_text("---\nname: healthy\n---\ny\n")

    real_parse_file = claude_command_agent.parse_file

    def flaky_parse_file(path, *args, **kwargs):
        if path.name == "broken.md":
            raise ValueError("simulated parse failure")
        return real_parse_file(path, *args, **kwargs)

    monkeypatch.setattr(claude_command_agent, "parse_file", flaky_parse_file)

    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 1
    assert refs[0].source_manifest.endswith("healthy.md")


def test_claude_only_agent_both_hosts_selected_single_occurrence_dual_host(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nhelp\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 1
    assert refs[0].extra["runtime_hosts"] == ["claude-code", "cursor"]


def test_claude_only_agent_cursor_not_selected_output_unchanged_from_today(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nhelp\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code"])
    assert len(refs) == 1
    assert "runtime_hosts" not in refs[0].extra


def test_claude_only_selection_with_cursor_sibling_output_unchanged(tmp_path):
    # A .cursor/agents override merely existing on disk must not change
    # Claude-only output: override splitting is Cursor-involved logic,
    # applied only when Cursor is selected. Byte-compat with today's
    # key-less extra dict.
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nc\n")
    cursor_agents = tmp_path / ".cursor" / "agents"
    cursor_agents.mkdir(parents=True)
    (cursor_agents / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code"])
    assert len(refs) == 1
    assert refs[0].source_manifest.endswith(".claude/agents/helper.md")
    assert "runtime_hosts" not in refs[0].extra


def test_cursor_only_scan_still_reads_claude_agents_compatibility(tmp_path):
    # Cursor's compatibility read is unconditional: a Cursor-only scan must
    # surface a .claude/agents file as a cursor-readable occurrence.
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nhelp\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["cursor"])
    assert len(refs) == 1
    assert refs[0].extra["runtime_hosts"] == ["cursor"]


def test_cursor_only_scan_override_suppresses_claude_copy(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nc\n")
    cursor_agents = tmp_path / ".cursor" / "agents"
    cursor_agents.mkdir(parents=True)
    (cursor_agents / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["cursor"])
    assert len(refs) == 1
    assert refs[0].source_manifest.endswith(".cursor/agents/helper.md")
    assert refs[0].extra["runtime_hosts"] == ["cursor"]


def test_override_present_two_single_host_occurrences(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nclaude version\n")
    cursor_agents = tmp_path / ".cursor" / "agents"
    cursor_agents.mkdir(parents=True)
    (cursor_agents / "helper.md").write_text("---\nname: helper\n---\ncursor version\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 2
    by_host = {tuple(r.extra["runtime_hosts"]): r for r in refs}
    assert by_host[("claude-code",)].source_manifest.endswith(".claude/agents/helper.md")
    assert by_host[("cursor",)].source_manifest.endswith(".cursor/agents/helper.md")


def test_cursor_only_agent_no_claude_counterpart(tmp_path):
    cursor_agents = tmp_path / ".cursor" / "agents"
    cursor_agents.mkdir(parents=True)
    (cursor_agents / "solo.md").write_text("---\nname: solo\n---\ncursor only\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 1
    assert refs[0].extra["runtime_hosts"] == ["cursor"]


def test_nested_relative_path_override_matched_correctly(tmp_path):
    # Same relative path under a nested project root, not just top-level.
    nested = tmp_path / "packages" / "frontend"
    (nested / ".claude" / "agents").mkdir(parents=True)
    (nested / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\n---\nc\n")
    (nested / ".cursor" / "agents").mkdir(parents=True)
    (nested / ".cursor" / "agents" / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    assert len(refs) == 2


def test_override_scopes_are_independent(tmp_path):
    # An override in one scope must not affect pairing in another: top-level
    # .claude/agents/helper.md has NO top-level .cursor override, so it stays
    # dual-host even though a nested scope has its own override pair.
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\n---\nc\n")
    nested = tmp_path / "packages" / "frontend"
    (nested / ".claude" / "agents").mkdir(parents=True)
    (nested / ".claude" / "agents" / "helper.md").write_text("---\nname: helper\n---\nc\n")
    (nested / ".cursor" / "agents").mkdir(parents=True)
    (nested / ".cursor" / "agents" / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences(tmp_path, hosts=["claude-code", "cursor"])
    hosts_by_manifest = {r.source_manifest: r.extra.get("runtime_hosts") for r in refs}
    top = str(tmp_path / ".claude" / "agents" / "helper.md")
    assert hosts_by_manifest[top] == ["claude-code", "cursor"]
    assert len(refs) == 3


def test_explicit_dirs_entry_point_ignores_directory_basenames(tmp_path):
    # Endpoint config roots are arbitrary paths — no `.claude`/`.cursor`
    # naming to discover from. The explicit-dirs entry point must pair by
    # relative path across whatever dirs the caller names.
    from tools.subagent_precedence import resolve_subagent_occurrences_for_dirs

    claude_dir = tmp_path / "claude-install" / "agents"
    claude_dir.mkdir(parents=True)
    (claude_dir / "helper.md").write_text("---\nname: helper\n---\nc\n")
    cursor_dir = tmp_path / "cursor-install" / "agents"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "helper.md").write_text("---\nname: helper\n---\nx\n")
    refs = resolve_subagent_occurrences_for_dirs(
        claude_dir, cursor_dir, hosts=["claude-code", "cursor"]
    )
    assert len(refs) == 2
    assert {tuple(r.extra["runtime_hosts"]) for r in refs} == {("claude-code",), ("cursor",)}
