import json
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


def test_plugin_self_identity_carries_ecosystem_for_matcher():
    """Plan 007: self-identity ref tags ecosystem='claude-plugin' so the
    matcher's _match_versioned path fires on plugin advisories."""
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    plugin_self = next(r for r in refs if r.ecosystem == "claude-plugin")
    assert plugin_self.name == "deployment-tools"
    assert plugin_self.version == "1.2.0"
    assert plugin_self.component_identity == "claude-plugin/deployment-tools@1.2.0"


def test_mcp_servers_string_path_resolves_from_plugin_root():
    """Plan 007 bug fix: mcpServers as a string path resolves from the plugin
    root (manifest.parent.parent), not the manifest's directory. Resolving
    from the manifest dir would land in `.claude-plugin/.mcp.json` instead
    of `<plugin-root>/.mcp.json`."""
    manifest = REPOS / "sample-plugin-string-mcp" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "@example/test-mcp"
    assert npm_refs[0].version == "1.0.0"


def test_mcp_servers_absolute_path_is_skipped(tmp_path):
    """An absolute path as mcpServers string must be silently rejected.

    Python's Path division replaces the root entirely for absolute paths:
    `plugin_root / "/abs/path"` yields `/abs/path`, not something inside
    the plugin root. The resolver must detect this via is_relative_to and
    skip the file rather than reading arbitrary host paths.
    """
    external = tmp_path / "external.mcp.json"
    external.write_text(
        json.dumps({"mcpServers": {"evil": {"command": "npx", "args": ["-y", "evil-pkg"]}}})
    )
    plugin_dir = tmp_path / "myplugin" / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(
        json.dumps({"name": "abs-plugin", "version": "1.0.0", "mcpServers": str(external)})
    )
    refs = parse(manifest)
    assert sum(1 for r in refs if r.ecosystem == "claude-plugin") == 1
    assert all(r.ecosystem != "npm" for r in refs)


def test_mcp_servers_traversal_path_is_skipped(tmp_path):
    """A relative path that escapes plugin_root via .. must be silently rejected."""
    external = tmp_path / "external.mcp.json"
    external.write_text(
        json.dumps({"mcpServers": {"evil": {"command": "npx", "args": ["-y", "evil-pkg"]}}})
    )
    plugin_dir = tmp_path / "myplugin" / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    # Traversal from <tmp>/myplugin/ up to <tmp>/ to reach external.mcp.json
    manifest.write_text(
        json.dumps(
            {"name": "trav-plugin", "version": "1.0.0", "mcpServers": "../external.mcp.json"}
        )
    )
    refs = parse(manifest)
    assert sum(1 for r in refs if r.ecosystem == "claude-plugin") == 1
    assert all(r.ecosystem != "npm" for r in refs)


def test_plugin_non_string_version_coerced_to_none(tmp_path):
    """A plugin.json with a numeric version (e.g. `"version": 1`) must not crash.
    The non-string value is coerced to None so the ref is still emitted without
    a version rather than propagating an integer to packaging.Version."""
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({"name": "my-plugin", "version": 1}))
    refs = parse(manifest)
    plugin_self = [r for r in refs if r.ecosystem == "claude-plugin"]
    assert len(plugin_self) == 1
    assert plugin_self[0].name == "my-plugin"
    assert plugin_self[0].version is None
    assert plugin_self[0].component_identity == "claude-plugin/my-plugin"


def test_mcp_servers_string_path_missing_target_does_not_raise(tmp_path):
    """If the string-path target file doesn't exist, the parser should
    silently skip rather than raise."""
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(
        '{"name": "missing-mcp-plugin", "version": "0.1.0", "mcpServers": "./.mcp.json"}'
    )
    # No .mcp.json file at the plugin root → just emit the self-identity ref.
    refs = parse(manifest)
    assert any(r.ecosystem == "claude-plugin" for r in refs)
    assert all(r.ecosystem != "npm" for r in refs)
