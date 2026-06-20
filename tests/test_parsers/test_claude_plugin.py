import json
from pathlib import Path

from tools.parsers.claude_plugin import parse, parse_at_install_root

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_plugin_self_identity():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    plugin_self = [
        r for r in refs if r.component_identity and r.component_identity.startswith("plugin/")
    ]
    assert len(plugin_self) == 1
    assert plugin_self[0].component_identity == "plugin/deployment-tools"


def test_plugin_dependencies():
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    deps = [
        r for r in refs if r.component_identity and r.component_identity.startswith("plugin-dep/")
    ]
    identities = {r.component_identity for r in deps}
    assert "plugin-dep/helper-lib" in identities
    assert "plugin-dep/secrets-vault@~2.1.0" in identities


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
        r for r in refs if r.component_identity and r.component_identity.startswith("plugin-dep/")
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


def test_plugin_self_identity_carries_component_type_not_ecosystem():
    """Plugin is an agent component type; source ecosystem is unknown unless
    the manifest or lockfile provides a real source coordinate."""
    manifest = REPOS / "sample-plugin" / ".claude-plugin" / "plugin.json"
    refs = parse(manifest)
    plugin_self = next(r for r in refs if r.component_identity == "plugin/deployment-tools")
    assert plugin_self.ecosystem is None
    assert plugin_self.extra["component_type"] == "plugin"
    assert plugin_self.name == "deployment-tools"
    assert plugin_self.version == "1.2.0"


def test_repo_mode_walks_default_bundled_skills(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "openaca", "version": "0.1.0"}))
    skill_dir = plugin_root / "skills" / "scan"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: scan\ndescription: Run OpenACA scans from Claude Code.\n---\n\n# Scan\n"
    )

    refs = parse(manifest)

    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert len(skill_refs) == 1
    assert skill_refs[0].component_identity == "skill/scan"


def test_default_skills_dir_symlink_outside_plugin_root_is_rejected(tmp_path):
    """A symlinked `skills/` that resolves outside the plugin root must be silently
    skipped. This mirrors the containment check already applied to mcpServers and
    custom skills paths via _resolve_within."""
    import os

    external_skills = tmp_path / "external_skills"
    skill_subdir = external_skills / "escape"
    skill_subdir.mkdir(parents=True)
    (skill_subdir / "SKILL.md").write_text(
        "---\nname: escape\ndescription: Escaped skill.\n---\n\n# Escape\n"
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "sym-plugin", "version": "1.0.0"}))
    # Symlink plugin_root/skills -> external_skills (outside plugin root)
    os.symlink(external_skills, plugin_root / "skills")

    refs = parse(manifest)
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert skill_refs == [], "Symlinked skills dir outside plugin root must be rejected"


def test_symlinked_skill_subdir_outside_plugin_root_is_rejected(tmp_path):
    """A symlinked skill subdir inside skills/ that resolves outside the plugin root
    must be skipped.  The top-level skills/ guard doesn't protect against symlinked
    children; each child dir is now also checked via containment before parsing."""
    import os

    external_skills = tmp_path / "external_skills"
    external_skill_dir = external_skills / "evil_skill"
    external_skill_dir.mkdir(parents=True)
    (external_skill_dir / "SKILL.md").write_text(
        "---\nname: evil\ndescription: Evil skill.\n---\n\n# Evil\n"
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "sym-plugin", "version": "1.0.0"}))
    skills_dir = plugin_root / "skills"
    skills_dir.mkdir()
    # Symlink plugin_root/skills/evil_skill -> external directory outside plugin root.
    os.symlink(external_skill_dir, skills_dir / "evil_skill")

    refs = parse(manifest)
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert skill_refs == [], "Symlinked skill subdir outside plugin root must be rejected"


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
    assert sum(1 for r in refs if r.extra.get("component_type") == "plugin") == 1
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
    assert sum(1 for r in refs if r.extra.get("component_type") == "plugin") == 1
    assert all(r.ecosystem != "npm" for r in refs)


def test_plugin_non_string_version_coerced_to_none(tmp_path):
    """A plugin.json with a numeric version (e.g. `"version": 1`) must not crash.
    The non-string value is coerced to None so the ref is still emitted without
    a version rather than propagating an integer to packaging.Version."""
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({"name": "my-plugin", "version": 1}))
    refs = parse(manifest)
    plugin_self = [r for r in refs if r.extra.get("component_type") == "plugin"]
    assert len(plugin_self) == 1
    assert plugin_self[0].name == "my-plugin"
    assert plugin_self[0].version is None
    assert plugin_self[0].component_identity == "plugin/my-plugin"


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
    assert any(r.extra.get("component_type") == "plugin" for r in refs)
    assert all(r.ecosystem != "npm" for r in refs)


# parse_at_install_root: endpoint-mode entry point.
# Identical inputs to repo-mode but path resolution is anchored at the
# install root rather than the manifest's parent directory.


def test_parse_at_install_root_returns_empty_when_plugin_json_absent(tmp_path):
    """No plugin.json at <install_root>/.claude-plugin/ — return []."""
    assert parse_at_install_root(tmp_path) == []


def test_parse_at_install_root_emits_dependencies_with_attribution(tmp_path):
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "p",
                "version": "1.0",
                "dependencies": ["helper-lib", {"name": "secrets-vault", "version": "2.1.0"}],
            }
        )
    )
    refs = parse_at_install_root(tmp_path)
    assert len(refs) == 2
    identities = sorted(r.component_identity or "" for r in refs)
    assert identities == ["plugin-dep/helper-lib", "plugin-dep/secrets-vault@2.1.0"]


def test_parse_at_install_root_emits_inline_mcp_servers_with_attribution(tmp_path):
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "p",
                "version": "1.0",
                "mcpServers": {"foo": {"command": "npx", "args": ["-y", "@example/foo@1.2.3"]}},
            }
        )
    )
    refs = parse_at_install_root(tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1


def test_parse_at_install_root_string_path_resolves_from_install_root(tmp_path):
    """A string-path mcpServers must resolve relative to install_root, not
    relative to <install_root>/.claude-plugin/. Verify by placing .mcp.json
    at <install_root>/.mcp.json (the install root) and pointing plugin.json
    at "./.mcp.json" — the repo-mode resolution would land at
    <install_root>/.claude-plugin/.mcp.json instead."""
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "p", "version": "1.0", "mcpServers": "./.mcp.json"})
    )
    # File at install root, NOT under .claude-plugin/.
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"foo": {"command": "npx", "args": ["-y", "@x/y@1.0"]}}})
    )
    refs = parse_at_install_root(tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "@x/y"


def test_parse_at_install_root_rejects_path_traversal(tmp_path):
    """A `../` in mcpServers must not escape the install root."""
    external = tmp_path / "outside.mcp.json"
    external.write_text(
        json.dumps({"mcpServers": {"evil": {"command": "npx", "args": ["-y", "evil-pkg"]}}})
    )
    install_root = tmp_path / "install"
    plugin_dir = install_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "trav", "version": "1.0", "mcpServers": "../outside.mcp.json"})
    )
    refs = parse_at_install_root(install_root)
    assert all(r.ecosystem != "npm" for r in refs)


def test_parse_at_install_root_does_not_emit_plugin_self_identity(tmp_path):
    """The endpoint caller emits the self-identity ref from the lockfile
    (more accurate version + sha than plugin.json). This function must NOT
    also emit a self-identity ref."""
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps({"name": "p", "version": "1.0"}))
    refs = parse_at_install_root(tmp_path)
    assert all(r.ecosystem != "claude-plugin" for r in refs)
    assert refs == []  # no deps, no mcpServers → empty


def test_parse_at_install_root_skips_malformed_plugin_json(tmp_path):
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text("{not json")
    assert parse_at_install_root(tmp_path) == []


def test_parse_at_install_root_skips_non_object_plugin_json(tmp_path):
    plugin_dir = tmp_path / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text("[]")
    assert parse_at_install_root(tmp_path) == []


def test_unreadable_skills_dir_does_not_drop_plugin_parse(tmp_path):
    """An unreadable skills/ directory must not abort parse() entirely.

    iterdir() on a mode-000 directory raises PermissionError (OSError subclass).
    Before the fix this propagated through _parse_bundled_skills() and silenced
    the whole plugin manifest in parse_repo_grouped().  After the fix, iterdir
    errors are caught per skills_dir so the plugin self-ref is still emitted.
    """
    import os
    import stat

    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "guarded-plugin", "version": "2.0.0"}))
    skills_dir = plugin_root / "skills"
    skills_dir.mkdir()
    # Make the directory unreadable so iterdir() raises PermissionError.
    os.chmod(skills_dir, 0o000)
    try:
        refs = parse(manifest)
    finally:
        os.chmod(skills_dir, stat.S_IRWXU)

    plugin_refs = [r for r in refs if r.component_identity == "plugin/guarded-plugin"]
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    # Running as root bypasses permission checks — skip the self-ref assertion
    # in that environment (the important thing is no exception is raised).
    if os.getuid() != 0:
        assert len(plugin_refs) == 1, "plugin self-ref must survive unreadable skills dir"
    assert skill_refs == [], "no skill refs expected from unreadable skills dir"


def test_symlinked_skill_md_outside_plugin_root_is_rejected(tmp_path):
    """A SKILL.md that is itself a symlink pointing outside the plugin root must
    be silently skipped.  The subdir containment check doesn't protect against a
    symlinked file entry inside an otherwise-valid skill subdir."""
    import os

    external_content = tmp_path / "external_skill.md"
    external_content.write_text(
        "---\nname: escape\ndescription: Escaped via SKILL.md symlink.\n---\n\n# Escape\n"
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "symlink-skill-md-plugin", "version": "1.0.0"}))
    skill_subdir = plugin_root / "skills" / "escape"
    skill_subdir.mkdir(parents=True)
    # SKILL.md is a symlink to a file outside the plugin root.
    os.symlink(external_content, skill_subdir / "SKILL.md")

    refs = parse(manifest)
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert skill_refs == [], "SKILL.md symlink escaping plugin root must be rejected"


def test_symlink_loop_skill_subdir_does_not_drop_plugin_parse(tmp_path):
    import os

    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "skill-loop-plugin", "version": "1.0.0"}))
    skills_dir = plugin_root / "skills"
    skills_dir.mkdir()
    os.symlink(skills_dir / "loop", skills_dir / "loop")

    refs = parse(manifest)

    plugin_refs = [r for r in refs if r.component_identity == "plugin/skill-loop-plugin"]
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert len(plugin_refs) == 1
    assert skill_refs == []


def test_symlinked_default_hooks_dir_outside_plugin_root_is_rejected(tmp_path):
    """A symlinked `hooks/` that resolves outside the plugin root must be silently
    skipped.  The default hooks path was accepted via bare is_file(); it now uses
    resolve_within so an external hooks.json is never ingested."""
    import os

    external_hooks_dir = tmp_path / "external_hooks"
    external_hooks_dir.mkdir()
    (external_hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "description": "External hooks",
                "hooks": {"PreToolUse": [{"type": "command", "command": "echo evil"}]},
            }
        )
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "hooks-escape-plugin", "version": "1.0.0"}))
    # Symlink plugin_root/hooks -> external_hooks_dir (outside plugin root)
    os.symlink(external_hooks_dir, plugin_root / "hooks")

    refs = parse(manifest)
    hook_refs = [r for r in refs if r.extra.get("component_type") == "hook"]
    assert hook_refs == [], "Symlinked hooks dir outside plugin root must be rejected"


def test_symlinked_default_commands_dir_outside_plugin_root_is_rejected(tmp_path):
    """A symlinked `commands/` that resolves outside the plugin root must be silently
    skipped.  The default commands path was accepted via bare is_dir(); it now uses
    resolve_within so external markdown files are never parsed as commands."""
    import os

    external_commands = tmp_path / "external_commands"
    external_commands.mkdir()
    (external_commands / "evil.md").write_text(
        "---\nname: evil-command\ndescription: Escaped command.\n---\n\n# Evil\n"
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "cmd-escape-plugin", "version": "1.0.0"}))
    # Symlink plugin_root/commands -> external_commands (outside plugin root)
    os.symlink(external_commands, plugin_root / "commands")

    refs = parse(manifest)
    command_refs = [r for r in refs if r.extra.get("component_type") == "command"]
    assert command_refs == [], "Symlinked commands dir outside plugin root must be rejected"


def test_symlinked_default_agents_dir_outside_plugin_root_is_rejected(tmp_path):
    """A symlinked `agents/` that resolves outside the plugin root must be silently
    skipped.  The default agents path was accepted via bare is_dir(); it now uses
    resolve_within so external markdown files are never parsed as agents."""
    import os

    external_agents = tmp_path / "external_agents"
    external_agents.mkdir()
    (external_agents / "evil.md").write_text(
        "---\nname: evil-agent\ndescription: Escaped agent.\n---\n\n# Evil\n"
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "agent-escape-plugin", "version": "1.0.0"}))
    # Symlink plugin_root/agents -> external_agents (outside plugin root)
    os.symlink(external_agents, plugin_root / "agents")

    refs = parse(manifest)
    agent_refs = [r for r in refs if r.extra.get("component_type") == "agent"]
    assert agent_refs == [], "Symlinked agents dir outside plugin root must be rejected"


def test_symlinked_default_mcp_file_outside_plugin_root_is_rejected(tmp_path):
    import os

    external_mcp = tmp_path / "external.mcp.json"
    external_mcp.write_text(
        json.dumps({"mcpServers": {"evil": {"command": "npx", "args": ["-y", "@evil/pkg@1.0.0"]}}})
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "mcp-escape-plugin", "version": "1.0.0"}))
    os.symlink(external_mcp, plugin_root / ".mcp.json")

    refs = parse(manifest)

    mcp_refs = [r for r in refs if r.extra.get("component_type") == "mcp_server"]
    assert mcp_refs == []


def test_symlinked_command_file_outside_plugin_root_is_rejected(tmp_path):
    import os

    external_command = tmp_path / "external-command.md"
    external_command.write_text(
        "---\nname: external-command\ndescription: Escaped command.\n---\n\n# External\n"
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "command-file-escape", "version": "1.0.0"}))
    commands_dir = plugin_root / "commands"
    commands_dir.mkdir()
    os.symlink(external_command, commands_dir / "external-command.md")

    refs = parse(manifest)

    command_refs = [r for r in refs if r.extra.get("component_type") == "command"]
    assert command_refs == []


def test_symlinked_agent_file_outside_plugin_root_is_rejected(tmp_path):
    import os

    external_agent = tmp_path / "external-agent.md"
    external_agent.write_text(
        "---\nname: external-agent\ndescription: Escaped agent.\n---\n\n# External\n"
    )
    plugin_root = tmp_path / "plugin"
    plugin_dir = plugin_root / ".claude-plugin"
    plugin_dir.mkdir(parents=True)
    manifest = plugin_dir / "plugin.json"
    manifest.write_text(json.dumps({"name": "agent-file-escape", "version": "1.0.0"}))
    agents_dir = plugin_root / "agents"
    agents_dir.mkdir()
    os.symlink(external_agent, agents_dir / "external-agent.md")

    refs = parse(manifest)

    agent_refs = [r for r in refs if r.extra.get("component_type") == "agent"]
    assert agent_refs == []
