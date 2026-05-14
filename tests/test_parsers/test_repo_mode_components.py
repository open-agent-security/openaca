"""Plan 008 Task 6: repo-mode component inventory.

The same parsers that fire in endpoint mode also fire in repo mode via the
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
    assert cmd_refs[0].component_identity == "claude-command/deploy"
    assert cmd_refs[0].attributed_to is None


def test_repo_mode_emits_declared_agent_with_frontmatter_name_override():
    """The fixture's agent file is named `reviewer.md` but the frontmatter
    declares `name: code-reviewer`. Identity should use the declared name."""
    refs = parse_repo(REPOS / "declared-components")
    agent_refs = [r for r in refs if r.ecosystem == "claude-agent"]
    assert len(agent_refs) == 1
    assert agent_refs[0].component_identity == "claude-agent/code-reviewer"
    assert agent_refs[0].attributed_to is None


def test_repo_mode_dedupes_mcp_when_plugin_json_string_path_overlaps():
    """The sample-plugin-string-mcp fixture has BOTH a .mcp.json at root AND
    a plugin.json that points at the same file via `mcpServers: "./.mcp.json"`.
    The registry walks both paths; without dedup, the same npm package would
    emit twice. parse_repo (via flatten_grouped) must dedup so matching and
    SARIF report each component once."""
    refs = parse_repo(REPOS / "sample-plugin-string-mcp")
    npm_refs = [r for r in refs if r.ecosystem == "npm" and r.name == "@example/test-mcp"]
    # Exactly one ref despite two discovery paths.
    assert len(npm_refs) == 1


def test_repo_mode_dedupes_mcp_with_relative_target(monkeypatch):
    """Dedup must work even when --target is a relative path.

    With a relative root, the direct rglob hit of .mcp.json produces a
    relative source_manifest string while _parse_mcp_servers_from_plugin_json
    calls Path.resolve() internally, yielding an absolute path. Without
    normalizing to absolute in flatten_grouped, the dedup key differs and
    the duplicate leaks through."""
    monkeypatch.chdir(REPOS)
    refs = parse_repo(Path("sample-plugin-string-mcp"))
    npm_refs = [r for r in refs if r.ecosystem == "npm" and r.name == "@example/test-mcp"]
    assert len(npm_refs) == 1
