from pathlib import Path

from tools.parsers.claude_plugin import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_plugin_self_identity():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    plugin_self = [
        r
        for r in refs
        if r.component_identity and r.component_identity.startswith("claude-plugin/")
    ]
    assert len(plugin_self) == 1
    assert plugin_self[0].component_identity == "claude-plugin/deployment-tools@1.2.0"


def test_plugin_dependencies():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    deps = [
        r
        for r in refs
        if r.component_identity and r.component_identity.startswith("claude-plugin-dep/")
    ]
    identities = {r.component_identity for r in deps}
    assert "claude-plugin-dep/helper-lib" in identities
    assert "claude-plugin-dep/secrets-vault@~2.1.0" in identities


def test_plugin_inlined_mcp_servers():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    npm_mcp = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_mcp) == 1
    assert npm_mcp[0].name == "@company/mcp-server"
    assert npm_mcp[0].version == "1.0.4"
    binary_mcp = [
        r
        for r in refs
        if r.component_identity and r.component_identity.startswith("mcp-stdio/binary:")
    ]
    assert len(binary_mcp) == 1


def test_dependencies_as_string_does_not_produce_bogus_refs(tmp_path):
    """Malformed `dependencies: "foo,bar"` must not iterate chars as dep names."""
    manifest = tmp_path / "plugin.json"
    manifest.write_text('{"name": "my-plugin", "dependencies": "foo,bar"}')
    refs = parse(manifest)
    dep_refs = [
        r
        for r in refs
        if r.component_identity and r.component_identity.startswith("claude-plugin-dep/")
    ]
    assert dep_refs == []


def test_top_level_array_does_not_raise(tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text("[]")
    assert parse(manifest) == []


def test_top_level_null_does_not_raise(tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text("null")
    assert parse(manifest) == []
