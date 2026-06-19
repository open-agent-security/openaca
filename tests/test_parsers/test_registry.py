from pathlib import Path

from tools.parsers import parse_repo

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_parse_repo_combines_all_manifests():
    refs = []
    for sample in ["sample-npm", "sample-mcp", "sample-plugin", "sample-settings"]:
        refs += parse_repo(REPOS / sample)

    purls = {r.purl for r in refs if r.purl}
    identities = {r.component_identity for r in refs if r.component_identity}

    assert "pkg:npm/%40cyanheads/git-mcp-server@1.1.0" in purls
    assert "pkg:pypi/weather-mcp@0.5.0" in purls
    assert "pkg:pypi/sketchy-mcp" in purls
    assert any(i.startswith("plugin/") for i in identities)


def test_one_malformed_manifest_does_not_abort_scan(tmp_path):
    """A repo with both a broken and a valid manifest should still emit refs."""
    (tmp_path / "package.json").write_text("not valid json")
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"git": {"command": "npx", "args": ["@scope/server@1.2.3"]}}}'
    )
    refs = parse_repo(tmp_path)
    purls = {r.purl for r in refs if r.purl}
    assert "pkg:npm/%40scope/server@1.2.3" in purls


def test_dep_manifest_without_plugin_marker_classified_as_software(tmp_path):
    """A bare package.json (no .claude-plugin/plugin.json sibling) → its deps
    are classified as software-dependency, surfacing the V0 scope split that
    the CLI uses to suppress noise from general software in repos that
    happen to also be Claude users."""
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}'
    )
    refs = parse_repo(tmp_path)
    scopes = {r.scope for r in refs if r.ecosystem == "npm"}
    assert scopes == {"software-dependency"}


def test_dep_manifest_co_located_with_plugin_classified_as_agent_dep(tmp_path):
    """The same package.json but with a sibling .claude-plugin/plugin.json
    becomes agent-dependency — these deps power a plugin's implementation
    and are in scope for V0."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"my-plugin","version":"1.0.0"}'
    )
    (tmp_path / "package.json").write_text(
        '{"name":"my-plugin","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}'
    )
    refs = parse_repo(tmp_path)
    npm_scopes = {r.scope for r in refs if r.ecosystem == "npm"}
    assert npm_scopes == {"agent-dependency"}
    # The plugin self-identity ref stays agent-component (unchanged path).
    cp_scopes = {r.scope for r in refs if r.extra.get("component_type") == "plugin"}
    assert cp_scopes == {"agent-component"}


def test_dep_manifest_co_located_with_skill_classified_as_agent_dep(tmp_path):
    """A package.json beside a SKILL.md (a skill's own implementation deps) is
    agent-dependency and in scope. Skills are agent components, so their bundled
    deps must be scanned (and OSV-queried), not filtered as general software."""
    skill_dir = tmp_path / ".claude" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: Deploys services\n---\nRun the deploy steps.\n"
    )
    (skill_dir / "package.json").write_text(
        '{"name":"deploy","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}'
    )
    refs = parse_repo(tmp_path)
    npm_scopes = {r.scope for r in refs if r.ecosystem == "npm"}
    assert npm_scopes == {"agent-dependency"}
