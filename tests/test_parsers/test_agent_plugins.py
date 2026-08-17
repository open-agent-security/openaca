import json
import os

from tools.parsers.agent_plugins import is_agent_plugins_manifest, parse


def test_is_agent_plugins_manifest_detects_schema(tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    assert is_agent_plugins_manifest(manifest) is True


def test_is_agent_plugins_manifest_rejects_other_schema(tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({"name": "demo"}))
    assert is_agent_plugins_manifest(manifest) is False


def test_is_agent_plugins_manifest_rejects_same_origin_non_schema_urls(tmp_path):
    # Detection is the full authoritative URL shape, never an origin-prefix
    # match: Step 6 dispatches on every bare plugin.json in the tree, so a
    # loose prefix would classify unrelated same-origin documents as plugins.
    manifest = tmp_path / "plugin.json"
    for bad in (
        "https://agent-plugins.org/schemas/not-a-schema",
        "https://agent-plugins.org/schemas/1.0.0/other.schema.json",
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json?x=1",
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json#frag",
        "https://agent-plugins.org/schemas/1.0.0/extra/plugin.schema.json",
        "https://agent-plugins.org/schemas//plugin.schema.json",
    ):
        manifest.write_text(json.dumps({"$schema": bad, "name": "demo"}))
        assert is_agent_plugins_manifest(manifest) is False, bad


def test_is_agent_plugins_manifest_accepts_any_version_segment(tmp_path):
    # Version acceptance is syntactic, not enumerated — see Step 4's note.
    manifest = tmp_path / "plugin.json"
    manifest.write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/2.3.1/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    assert is_agent_plugins_manifest(manifest) is True


def test_parse_walks_skills_and_mcp_only(tmp_path):
    manifest = tmp_path / "plugin.json"
    manifest.write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "demo",
            }
        )
    )
    skills_dir = tmp_path / "skills" / "helper"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: helper\ndescription: d\n---\nrun\n")
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    # A commands/ dir present must be IGNORED — not part of the portable v1 contract.
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "deploy.md").write_text("run\n")

    refs = parse(manifest, runtime_hosts=["cursor"])
    kinds = {r.extra.get("component_type") for r in refs}
    assert "skill" in kinds
    assert "mcp_server" in kinds
    assert "command" not in kinds

    self_ref = next(r for r in refs if r.extra.get("component_type") == "plugin")
    assert self_ref.component_identity == "plugin/demo"
    assert self_ref.extra["runtime_hosts"] == ["cursor"]


def test_symlinked_mcp_json_outside_plugin_root_is_rejected(tmp_path):
    # Mirrors the skills/<name> and SKILL.md containment checks above: a
    # bundle's mcp.json can itself be a symlink escaping plugin_root, and
    # must not have its target's servers attributed to the plugin.
    external_mcp = tmp_path / "external-mcp.json"
    external_mcp.write_text(
        json.dumps({"mcpServers": {"evil": {"command": "npx", "args": ["-y", "@evil/pkg@1.0.0"]}}})
    )
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    manifest = plugin_root / "plugin.json"
    manifest.write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "mcp-escape-plugin",
            }
        )
    )
    os.symlink(external_mcp, plugin_root / "mcp.json")

    refs = parse(manifest)

    mcp_refs = [r for r in refs if r.extra.get("component_type") == "mcp_server"]
    assert mcp_refs == []
