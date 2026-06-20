"""Tests for the install-state-aware Claude Code resolver.

Plan 007 scope: minimal active-plugin emission. Bundled-component walking
and lockfile transitive scanning are plans 008 and 009.
"""

import json
from pathlib import Path

from tools.parsers.claude_install import parse_install

FIXTURES = Path(__file__).parent.parent / "fixtures" / "installs"


def test_minimal_install_emits_one_plugin_component():
    refs, warnings = parse_install(install_root=FIXTURES / "minimal")
    assert warnings == []

    plugin_refs = [r for r in refs if r.extra.get("component_type") == "plugin"]
    assert len(plugin_refs) == 1
    ref = plugin_refs[0]
    assert ref.name == "sample-plugin"
    assert ref.version == "1.2.0"
    assert ref.component_identity == "plugin/test-marketplace/sample-plugin"
    assert ref.extra["gitCommitSha"] == "deadbeef1234"
    assert ref.extra["marketplace"] == "test-marketplace"
    assert ref.extra["scope"] == "user"
    assert ref.source_locator == "$.plugins.sample-plugin@test-marketplace[0]"


def test_install_skips_disabled_plugins(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": False}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {"foo@bar": [{"scope": "user", "version": "1.0", "installPath": "/x"}]},
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert warnings == []


def test_install_warns_when_plugin_enabled_but_missing_from_lockfile(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"missing@nowhere": True}})
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {}})
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("missing@nowhere" in w for w in warnings)


def test_install_handles_missing_lockfile_silently(tmp_path):
    """If installed_plugins.json doesn't exist, return empty refs without
    raising — the install root may be malformed, not a crash condition."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert warnings == []


def test_install_warns_on_malformed_lockfile(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text("{not json")
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("malformed" in w for w in warnings)


def test_install_multi_entry_prefers_matching_scope(tmp_path):
    """Two install entries; the one whose `scope` matches the enabling scope
    (user, in this case) wins."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "foo@bar": [
                        {"scope": "project", "version": "1.0", "installPath": "/x"},
                        {"scope": "user", "version": "2.0", "installPath": "/y"},
                    ]
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert len(refs) == 1
    assert refs[0].version == "2.0"  # matching user scope wins
    assert refs[0].source_locator == "$.plugins.foo@bar[1]"
    assert warnings == []


def test_install_multi_entry_no_scope_match_falls_back_with_warning(tmp_path):
    """No entry's scope matches the enabling user scope (entries are
    project + managed). Fall back to [0] and warn."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "foo@bar": [
                        {"scope": "project", "version": "1.0", "installPath": "/x"},
                        {"scope": "managed", "version": "2.0", "installPath": "/y"},
                    ]
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert len(refs) == 1
    assert refs[0].version == "1.0"  # fallback to [0]
    assert any("foo@bar" in w and "no scope match" in w for w in warnings)


def test_install_repo_mode_excludes_local_scope_for_entry_selection(tmp_path):
    """In repo mode, local scope must be ignored when selecting an install entry.

    If a plugin is enabled in both local and project scopes, and installed_plugins
    has entries for both scopes, repo mode must pick the project-scope entry — not
    the local-scope entry (which has higher precedence in SCOPE_PRECEDENCE but is
    machine-local and not CI-relevant).
    """
    project_root = tmp_path / "project"
    claude_dir = project_root / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (claude_dir / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {"foo@bar": True}})
    )
    (tmp_path / "settings.json").write_text(json.dumps({}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "foo@bar": [
                        {"scope": "project", "version": "1.0", "installPath": "/x"},
                        {"scope": "local", "version": "2.0", "installPath": "/y"},
                    ]
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path, project_root=project_root, mode="repo")
    assert len(refs) == 1
    assert refs[0].version == "1.0"  # project-scope entry; local must not win
    assert warnings == []


def test_install_warns_on_non_object_lockfile(tmp_path):
    """installed_plugins.json is valid JSON but not an object (e.g. a list after a bad edit)."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text("[]")
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("top level" in w for w in warnings)


def test_install_skips_non_dict_install_entries(tmp_path):
    """Malformed lockfile where plugin entries are not dicts should warn + skip,
    not crash with AttributeError."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {"foo@bar": ["bad"]}})
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("foo@bar" in w and "no valid install entries" in w for w in warnings)


def test_install_treats_only_boolean_true_as_enabled(tmp_path):
    """Non-boolean truthy values in enabledPlugins must NOT enable a plugin.

    Claude Code settings are machine-generated but can be hand-edited. A user
    might write `"false"` (string), `1` (int), or `{}` (dict) by mistake.
    Only the literal JSON `true` (Python `True`) should enable a plugin;
    anything else is treated as disabled to avoid false-positive findings.
    This is consistent with `_enabling_scope`, which already uses `is True`.
    """
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "string-false@m": [{"scope": "user", "version": "1.0", "installPath": "/a"}],
                    "int-one@m": [{"scope": "user", "version": "1.0", "installPath": "/b"}],
                    "empty-dict@m": [{"scope": "user", "version": "1.0", "installPath": "/c"}],
                },
            }
        )
    )
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {
                    "string-false@m": "false",  # truthy string — must NOT enable
                    "int-one@m": 1,  # truthy int   — must NOT enable
                    "empty-dict@m": {},  # falsy dict   — must NOT enable
                }
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert warnings == []


def test_install_skips_entry_with_non_string_version(tmp_path):
    """A lockfile entry with a non-string version (e.g. integer 1) must warn and
    skip the ref. If propagated, packaging.Version raises TypeError and aborts
    `openaca scan endpoint`."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": {"foo@bar": [{"scope": "user", "version": 1}]}})
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("non-string version" in w and "foo@bar" in w for w in warnings)


def test_install_handles_plugin_key_without_marketplace_suffix(tmp_path):
    """Defensive: a plugin key without `@marketplace` shouldn't crash."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"orphan-plugin": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "orphan-plugin": [{"scope": "user", "version": "0.1", "installPath": "/x"}]
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert len(refs) == 1
    assert refs[0].name == "orphan-plugin"
    assert refs[0].extra["marketplace"] is None
    assert refs[0].component_identity == "plugin/orphan-plugin"


def test_install_scoped_plugin_key_parses_correctly(tmp_path):
    """Scoped plugin keys like `@acme/tool@test-market` must parse as
    name=`@acme/tool`, marketplace=`test-market` (rsplit from right),
    NOT name=`` + marketplace=`acme/tool@test-market` (split from left)."""
    plugin_key = "@acme/tool@test-market"
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {plugin_key: True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {plugin_key: [{"scope": "user", "version": "1.0", "installPath": "/x"}]},
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert len(refs) == 1
    assert refs[0].name == "@acme/tool"
    assert refs[0].extra["marketplace"] == "test-market"
    assert refs[0].component_identity == "plugin/test-market/@acme/tool"


def test_install_same_plugin_name_different_marketplaces_have_distinct_identities(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {
                    "shared@market-one": True,
                    "shared@market-two": True,
                }
            }
        )
    )
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "shared@market-one": [
                        {"scope": "user", "version": "1.0.0", "installPath": "/x"}
                    ],
                    "shared@market-two": [
                        {"scope": "user", "version": "1.0.0", "installPath": "/y"}
                    ],
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert warnings == []
    identities = {r.component_identity for r in refs if r.extra.get("component_type") == "plugin"}
    assert identities == {
        "plugin/market-one/shared",
        "plugin/market-two/shared",
    }


def test_install_warns_on_non_object_plugins_map(tmp_path):
    """When `installed_plugins.json` has a non-dict `plugins` value (e.g. a list),
    warn and return empty refs rather than silently missing findings."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 1, "plugins": ["should", "be", "an", "object"]})
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("plugins" in w and "not an object" in w for w in warnings)


def test_install_source_locator_preserves_original_index_after_filtering(tmp_path):
    """If installed_plugins.json has a malformed (non-dict) entry before a
    valid one, the emitted source_locator must reference the real lockfile
    index, not the post-filter position. Otherwise findings + debugging
    evidence point at the wrong array slot."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "foo@bar": [
                        "malformed-leading-entry",
                        {"scope": "user", "version": "2.0", "installPath": "/y"},
                    ]
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert len(refs) == 1
    assert refs[0].version == "2.0"
    # Index [1] is the real lockfile slot for the chosen entry, even after
    # the malformed [0] was filtered out of consideration.
    assert refs[0].source_locator == "$.plugins.foo@bar[1]"


def test_install_warns_on_unreadable_lockfile(tmp_path):
    """If installed_plugins.json exists but read_text raises (e.g.,
    PermissionError on a root-owned file), degrade with a warning rather
    than aborting the scan."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    # A directory at the lockfile path makes read_text raise IsADirectoryError
    # (a concrete OSError subclass) — easier to construct portably than a
    # permission-locked file in pytest.
    (plugins_dir / "installed_plugins.json").mkdir()
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("unreadable" in w for w in warnings)


def test_install_warns_on_non_utf8_lockfile(tmp_path):
    """Non-UTF-8 bytes in installed_plugins.json must degrade with a
    warning, not propagate UnicodeDecodeError out of the resolver."""
    (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}))
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "installed_plugins.json").write_bytes(b'\xff\xfe{\x00"\x00')
    refs, warnings = parse_install(install_root=tmp_path)
    assert refs == []
    assert any("decode error" in w for w in warnings)


# Plan 008: bundled-component walking inside active plugin installPaths.


def _build_install_with_plugin(
    tmp_path: Path,
    plugin_key: str,
    plugin_name: str,
    version: str,
) -> Path:
    """Build a minimal endpoint-mode install layout pointing at a real cache dir.

    Returns the installPath (so tests can populate bundled components inside
    it).
    """
    install_path = tmp_path / "cache" / plugin_name / version
    install_path.mkdir(parents=True)
    settings = {"enabledPlugins": {plugin_key: True}}
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    plugin_key: [
                        {
                            "scope": "user",
                            "version": version,
                            "installPath": str(install_path),
                            "gitCommitSha": "abc123",
                        }
                    ]
                },
            }
        )
    )
    return install_path


def test_install_walks_bundled_skill(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    skill_dir = install_path / "skills" / "bootstrap"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bootstrap\ndescription: scaffold a project\n---\nbody\n"
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert warnings == []
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert len(skill_refs) == 1
    assert skill_refs[0].name == "bootstrap"


def test_install_bundled_skill_carries_container_metadata(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    skill_dir = install_path / "skills" / "bootstrap"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bootstrap\ndescription: scaffold a project\n---\nbody\n"
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert warnings == []
    skill = next(r for r in refs if r.extra.get("component_type") == "skill")
    assert skill.extra["declared_by"]["kind"] == "plugin"
    assert skill.extra["declared_by"]["name"] == "superpowers"
    assert skill.extra["component_path"][0] == {"type": "plugin", "name": "superpowers"}
    assert skill.extra["component_path"][-1] == {"type": "skill", "name": "bootstrap"}
    assert skill.extra["runtime_hosts"] == ["claude-code"]


def test_install_walks_bundled_hooks(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    hooks_dir = install_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "description": "superpowers hooks",
                "hooks": {"PreToolUse": [{"type": "command", "command": "echo pre"}]},
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert warnings == []
    hook_refs = [r for r in refs if r.extra.get("component_type") == "hook"]
    assert len(hook_refs) == 1
    assert (hook_refs[0].component_identity or "").startswith("claude-hook/command:")
    assert hook_refs[0].extra["event"] == "PreToolUse"
    assert hook_refs[0].extra["index"] == 0


def test_install_walks_bundled_commands_and_agents(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    (install_path / "commands").mkdir()
    (install_path / "commands" / "deploy.md").write_text("deploy command body\n")
    (install_path / "agents").mkdir()
    (install_path / "agents" / "code-reviewer.md").write_text("reviewer body\n")
    refs, _ = parse_install(install_root=tmp_path)
    cmd_refs = [r for r in refs if r.extra.get("component_type") == "command"]
    agent_refs = [r for r in refs if r.extra.get("component_type") == "agent"]
    assert len(cmd_refs) == 1
    assert cmd_refs[0].component_identity == "claude-command/superpowers/deploy"
    assert len(agent_refs) == 1
    assert agent_refs[0].component_identity == "claude-agent/superpowers/code-reviewer"


def test_install_walks_bundled_default_mcp(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    (install_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"foo": {"command": "npx", "args": ["-y", "@x/y@1.0.0"]}}})
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "@x/y"
    assert npm_refs[0].version == "1.0.0"


def test_install_walks_plugin_json_inline_mcp_servers(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    cp_dir = install_path / ".claude-plugin"
    cp_dir.mkdir()
    (cp_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "superpowers",
                "version": "5.1.0",
                "mcpServers": {"foo": {"command": "npx", "args": ["-y", "@a/b@2.0.0"]}},
            }
        )
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "@a/b"


def test_install_does_not_double_emit_when_plugin_json_points_at_default_mcp(tmp_path):
    """If plugin.json says mcpServers='./.mcp.json' and that file exists at
    the install root, the same file gets walked once via parse_at_install_root
    and skipped by the default-path walk via dedupe on (source_manifest, identity)."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    cp_dir = install_path / ".claude-plugin"
    cp_dir.mkdir()
    (cp_dir / "plugin.json").write_text(
        json.dumps({"name": "superpowers", "version": "5.1.0", "mcpServers": "./.mcp.json"})
    )
    (install_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"foo": {"command": "npx", "args": ["-y", "@a/b@2.0.0"]}}})
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1


def test_install_walks_dependencies_from_plugin_json(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    cp_dir = install_path / ".claude-plugin"
    cp_dir.mkdir()
    (cp_dir / "plugin.json").write_text(
        json.dumps({"name": "superpowers", "version": "5.1.0", "dependencies": ["helper-lib"]})
    )
    refs, _ = parse_install(install_root=tmp_path)
    dep_refs = [
        r for r in refs if r.component_identity and r.component_identity.startswith("plugin-dep/")
    ]
    assert len(dep_refs) == 1


def test_install_bun_lock_takes_priority_over_package_lock_json(tmp_path):
    """When both bun.lock and package-lock.json exist, bun.lock wins and
    package-lock.json is not parsed — the same dependency is not reported twice."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="bun-plugin@m", plugin_name="bun-plugin", version="1.0.0"
    )
    (install_path / "bun.lock").write_text(
        json.dumps(
            {
                "lockfileVersion": 1,
                "workspaces": {"": {"name": "bun-plugin", "dependencies": {"ms": "2.1.3"}}},
                "packages": {"ms": ["ms@2.1.3", "", {}, "sha512-bun=="]},
            }
        )
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "bun-plugin", "version": "1.0.0"},
                    "node_modules/ms": {"version": "2.1.3"},
                    "node_modules/extra": {"version": "1.0.0"},
                },
            }
        )
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    # Only one source for npm: bun.lock (ms@2.1.3). package-lock.json skipped.
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "ms"
    assert npm_refs[0].source_manifest.endswith("bun.lock")


def test_install_direct_mcp_from_user_settings(tmp_path):
    """`settings.json.mcpServers` (user scope) → emit npm/PyPI refs with no
    attribution (direct MCPs are declared directly by the user)."""
    (tmp_path / "settings.json").write_text(
        json.dumps({"mcpServers": {"foo": {"command": "npx", "args": ["-y", "@a/b@1.0.0"]}}})
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].source_manifest == str(tmp_path / "settings.json")


def test_install_direct_hooks_per_scope_emit_distinct_identities(tmp_path):
    """User and local scopes both declaring a hook → two refs with different
    identity scopes. Hooks are NOT merged across scopes."""
    project = tmp_path / "project"
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True)
    (tmp_path / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"type": "command", "command": "echo user"}]}})
    )
    (claude_dir / "settings.local.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"type": "command", "command": "echo local"}]}})
    )
    refs, _ = parse_install(install_root=tmp_path, project_root=project)
    hook_refs = sorted(
        (r for r in refs if r.extra.get("component_type") == "hook"),
        key=lambda r: r.component_identity or "",
    )
    assert len(hook_refs) == 2
    assert {r.extra["scope"] for r in hook_refs} == {"local", "user"}
    assert {r.extra["event"] for r in hook_refs} == {"PreToolUse"}
    assert all((r.component_identity or "").startswith("claude-hook/command:") for r in hook_refs)


def test_install_direct_hooks_repo_mode_excludes_local(tmp_path):
    """In repo mode, settings.local.json hooks are not emitted (machine-local)."""
    project = tmp_path / "project"
    claude_dir = project / ".claude"
    claude_dir.mkdir(parents=True)
    (tmp_path / "settings.json").write_text(json.dumps({}))
    (claude_dir / "settings.local.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"type": "command", "command": "echo local"}]}})
    )
    refs, _ = parse_install(install_root=tmp_path, project_root=project, mode="repo")
    assert all(r.extra.get("component_type") != "hook" for r in refs)


def test_install_direct_skills_from_install_root_skills_dir(tmp_path):
    """`<install_root>/skills/<name>/SKILL.md` → emit skill refs, no attribution."""
    skill_dir = tmp_path / "skills" / "linter"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: linter\ndescription: lints code\n---\nbody\n")
    refs, _ = parse_install(install_root=tmp_path)
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert len(skill_refs) == 1
    assert skill_refs[0].name == "linter"


def test_install_walks_direct_user_commands_and_agents(tmp_path):
    (tmp_path / "commands" / "frontend").mkdir(parents=True)
    (tmp_path / "commands" / "frontend" / "deploy.md").write_text("deploy\n")
    (tmp_path / "agents" / "review").mkdir(parents=True)
    (tmp_path / "agents" / "review" / "code.md").write_text(
        "---\nname: code-reviewer\n---\nreview\n"
    )

    refs, warnings = parse_install(install_root=tmp_path)

    assert warnings == []
    assert any(r.component_identity == "claude-command/deploy" for r in refs)
    assert any(r.component_identity == "claude-agent/code-reviewer" for r in refs)


def test_install_walks_project_skills_commands_and_agents(tmp_path):
    project = tmp_path / "project"
    (project / ".claude" / "skills" / "project-skill").mkdir(parents=True)
    (project / ".claude" / "skills" / "project-skill" / "SKILL.md").write_text(
        "---\nname: project-skill\nmetadata:\n  version: 1.0.0\n---\nbody\n"
    )
    (project / ".claude" / "commands" / "ops").mkdir(parents=True)
    (project / ".claude" / "commands" / "ops" / "deploy.md").write_text("deploy\n")
    (project / ".claude" / "agents" / "review").mkdir(parents=True)
    (project / ".claude" / "agents" / "review" / "code.md").write_text(
        "---\nname: code-reviewer\n---\nreview\n"
    )

    refs, warnings = parse_install(install_root=tmp_path, project_root=project)

    assert warnings == []
    assert any(r.component_identity == "skill/project-skill@1.0.0" for r in refs)
    assert any(r.component_identity == "claude-command/deploy" for r in refs)
    assert any(r.component_identity == "claude-agent/code-reviewer" for r in refs)


def test_install_walks_nested_project_skills(tmp_path):
    project = tmp_path / "project"
    skill_dir = project / "packages" / "frontend" / ".claude" / "skills" / "ui-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: ui-review\n---\nbody\n")

    refs, warnings = parse_install(install_root=tmp_path, project_root=project)

    assert warnings == []
    assert any(r.component_identity == "skill/ui-review" for r in refs)


def test_install_project_skills_skip_gitignored_worktrees(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitignore").write_text(".worktrees/\n")
    real_skill = project / ".claude" / "skills" / "bootstrap"
    real_skill.mkdir(parents=True)
    (real_skill / "SKILL.md").write_text("---\nname: bootstrap\n---\nbody\n")
    worktree_fixture = (
        project
        / ".worktrees"
        / "feature"
        / "tests"
        / "fixtures"
        / ".claude"
        / "skills"
        / "bootstrap"
    )
    worktree_fixture.mkdir(parents=True)
    (worktree_fixture / "SKILL.md").write_text("---\nname: bootstrap\n---\nbody\n")

    refs, warnings = parse_install(install_root=tmp_path, project_root=project)

    assert warnings == []
    bootstrap_refs = [r for r in refs if r.component_identity == "skill/bootstrap"]
    assert len(bootstrap_refs) == 1
    assert bootstrap_refs[0].source_manifest == str(real_skill / "SKILL.md")


def test_install_project_scoped_mcp_json(tmp_path):
    """`<project_root>/.mcp.json` is a project-shared MCP config — emit it."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"foo": {"command": "npx", "args": ["-y", "@p/q@1.0"]}}})
    )
    refs, _ = parse_install(install_root=tmp_path, project_root=project)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "@p/q"


def test_install_walks_inline_plugin_json_hooks(tmp_path):
    """A plugin declaring hooks in plugin.json (not hooks/hooks.json) emits
    claude-hook refs. Regression test for the P2 Codex fix."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    cp_dir = install_path / ".claude-plugin"
    cp_dir.mkdir()
    (cp_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "superpowers",
                "version": "5.1.0",
                "hooks": {"PostToolUse": [{"type": "command", "command": "echo post"}]},
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert warnings == []
    hook_refs = [r for r in refs if r.extra.get("component_type") == "hook"]
    assert len(hook_refs) == 1
    assert (hook_refs[0].component_identity or "").startswith("claude-hook/command:")
    assert hook_refs[0].extra["event"] == "PostToolUse"
    assert hook_refs[0].source_manifest.endswith("plugin.json")


def test_install_emits_both_file_and_inline_plugin_hooks(tmp_path):
    """A plugin with both hooks/hooks.json AND inline plugin.json hooks emits
    refs from both sources — no deduplication is applied."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    hooks_dir = install_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "description": "superpowers hooks",
                "hooks": {"PreToolUse": [{"type": "command", "command": "echo pre"}]},
            }
        )
    )
    cp_dir = install_path / ".claude-plugin"
    cp_dir.mkdir()
    (cp_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "superpowers",
                "version": "5.1.0",
                "hooks": {"PostToolUse": [{"type": "command", "command": "echo post"}]},
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert warnings == []
    hook_refs = sorted(
        (r for r in refs if r.extra.get("component_type") == "hook"),
        key=lambda r: r.component_identity or "",
    )
    assert len(hook_refs) == 2
    assert {r.extra["event"] for r in hook_refs} == {"PostToolUse", "PreToolUse"}
    assert len({r.component_identity for r in hook_refs}) == 2


def test_install_silent_when_installpath_missing(tmp_path):
    """Stale lockfile pointing at a deleted install_path: emit the
    self-identity ref from the lockfile, walk nothing bundled, no warnings.
    (Verbose output shows zero bundled-counts; that's the signal.)"""
    settings = {"enabledPlugins": {"orphan@m": True}}
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 1,
                "plugins": {
                    "orphan@m": [
                        {
                            "scope": "user",
                            "version": "1.0",
                            "installPath": str(tmp_path / "does-not-exist"),
                        }
                    ]
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    assert warnings == []
    plugin_refs = [r for r in refs if r.extra.get("component_type") == "plugin"]
    assert len(plugin_refs) == 1
    # No bundled refs.
    assert all(r.extra.get("component_type") != "skill" for r in refs)
    assert all(r.extra.get("component_type") != "hook" for r in refs)


# Plan 009 Task 4: Tier-2 endpoint-mode dispatch (lockfile + manifest fallback).


def test_install_emits_npm_lockfile_refs_for_active_plugin(tmp_path):
    """A plugin with package-lock.json at its installPath emits transitive
    npm refs, all attributed to the plugin."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "webp", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    refs, warnings = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "lodash"
    assert npm_refs[0].version == "4.17.20"
    assert npm_refs[0].extra["transitive"] is True


def test_install_falls_back_to_package_json_when_no_lockfile(tmp_path):
    """No package-lock.json but package.json exists → emit direct deps with
    transitive=False and a fallback_reason."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package.json").write_text(
        json.dumps({"name": "webp", "dependencies": {"lodash": "^4.17.0"}})
    )
    refs, warnings = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "lodash"
    assert npm_refs[0].extra.get("transitive") is False
    assert "no npm lockfile" in (npm_refs[0].extra.get("fallback_reason") or "")


def test_install_parses_both_npm_and_pypi_lockfiles_per_plugin(tmp_path):
    """A plugin shipping JS + embedded Python: parse BOTH lockfiles, not
    first-match. Validates ADR-0008's parse-all-lockfiles decision."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="multi@m", plugin_name="multi", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "multi", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    (install_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "requests"\nversion = "2.31.0"\n'
    )
    refs, _ = parse_install(install_root=tmp_path)
    ecosystems = {r.ecosystem for r in refs}
    assert "npm" in ecosystems
    assert "PyPI" in ecosystems


def test_install_lockfile_wins_when_both_lockfile_and_manifest_present(tmp_path):
    """Lockfile gets parsed; manifest fallback skipped for the covered ecosystem."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "webp", "version": "1.0.0"},
                    "node_modules/from-lock": {"version": "1.0.0"},
                },
            }
        )
    )
    (install_path / "package.json").write_text(
        json.dumps({"name": "webp", "dependencies": {"from-manifest": "^1.0.0"}})
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_names = {r.name for r in refs if r.ecosystem == "npm"}
    assert "from-lock" in npm_names
    assert "from-manifest" not in npm_names


def test_install_include_transitive_false_skips_lockfile_and_manifest(tmp_path):
    """When include_transitive=False, Tier-2 walk is skipped entirely;
    Tier-1 components still emit."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "webp", "version": "1.0.0"},
                    "node_modules/lodash": {"version": "4.17.20"},
                },
            }
        )
    )
    skill_dir = install_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: x\n---\nbody\n")
    refs, _ = parse_install(install_root=tmp_path, include_transitive=False)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert npm_refs == []
    assert len(skill_refs) == 1  # Tier-1 still emitted


def test_install_transitive_lockfile_refs_have_agent_dependency_scope(tmp_path):
    """Tier-2 refs from _walk_plugin_implementation_deps must carry
    scope='agent-dependency' so the BOM emits them as CycloneDX library
    components with openaca:component_type=package."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "webp", "version": "1.0.0"},
                    "node_modules/sharp": {"version": "0.33.0"},
                },
            }
        )
    )
    refs, _ = parse_install(install_root=tmp_path, include_transitive=True)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "sharp"
    assert npm_refs[0].scope == "agent-dependency"


def test_install_pyproject_fallback_when_no_uv_lock(tmp_path):
    """No uv.lock but pyproject.toml exists → emit direct deps with transitive=False."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="pyp@m", plugin_name="pyp", version="1.0.0"
    )
    (install_path / "pyproject.toml").write_text(
        '[project]\nname = "pyp"\nversion = "1.0.0"\ndependencies = ["requests==2.31.0"]\n'
    )
    refs, _ = parse_install(install_root=tmp_path)
    pypi_refs = [r for r in refs if r.ecosystem == "PyPI"]
    assert any(r.name == "requests" for r in pypi_refs)
    requests_ref = next(r for r in pypi_refs if r.name == "requests")
    assert requests_ref.extra.get("transitive") is False


def test_install_v1v2_lockfile_falls_back_to_manifest(tmp_path):
    """P1 regression: npm v1/v2 lockfile (no 'packages' key) returns [] from the
    parser — manifest fallback must still fire rather than being silently suppressed."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    # v1/v2 lockfile: has 'dependencies' key, not 'packages' → parser returns []
    (install_path / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 1, "dependencies": {"lodash": {"version": "4.17.20"}}})
    )
    (install_path / "package.json").write_text(
        json.dumps({"name": "webp", "dependencies": {"lodash": "^4.17.0"}})
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    # v1/v2 lockfile yields nothing; manifest fallback should kick in
    assert len(npm_refs) == 1
    assert npm_refs[0].name == "lodash"
    assert npm_refs[0].extra.get("transitive") is False


def test_install_pyproject_fallback_excludes_optional_and_dev_groups(tmp_path):
    """P2 regression: pyproject.toml fallback (no uv.lock) must emit only
    project.dependencies — optional-dependencies and dependency-groups are
    non-runtime and should be filtered by _RUNTIME_MANIFEST_LOCATORS."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="pyp@m", plugin_name="pyp", version="1.0.0"
    )
    (install_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "pyp"\n'
        'version = "1.0.0"\n'
        'dependencies = ["requests==2.31.0"]\n'
        "\n"
        "[project.optional-dependencies]\n"
        'dev = ["pytest==7.4.0"]\n'
        'typing = ["mypy==1.8.0"]\n'
        "\n"
        "[dependency-groups]\n"
        'lint = ["ruff==0.3.0"]\n'
    )
    refs, _ = parse_install(install_root=tmp_path)
    pypi_refs = [r for r in refs if r.ecosystem == "PyPI"]
    pypi_names = {r.name for r in pypi_refs}
    assert "requests" in pypi_names
    assert "pytest" not in pypi_names
    assert "mypy" not in pypi_names
    assert "ruff" not in pypi_names


def test_install_manifest_fallback_excludes_dev_dependencies(tmp_path):
    """P2 regression: manifest fallback for npm must only emit 'dependencies',
    not devDependencies / peerDependencies / optionalDependencies."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="webp@m", plugin_name="webp", version="1.0.0"
    )
    (install_path / "package.json").write_text(
        json.dumps(
            {
                "name": "webp",
                "dependencies": {"lodash": "^4.17.0"},
                "devDependencies": {"jest": "^29.0.0"},
                "peerDependencies": {"react": "^18.0.0"},
                "optionalDependencies": {"fsevents": "^2.0.0"},
            }
        )
    )
    refs, _ = parse_install(install_root=tmp_path)
    npm_refs = [r for r in refs if r.ecosystem == "npm"]
    npm_names = {r.name for r in npm_refs}
    assert "lodash" in npm_names
    assert "jest" not in npm_names
    assert "react" not in npm_names
    assert "fsevents" not in npm_names


# ── Custom plugin.json path handling ─────────────────────────────────────────
#
# Real plugins (e.g., supabase) declare custom paths in plugin.json for
# `skills`, `commands`, `agents`, and `hooks`. Per Claude Code semantics,
# custom paths *merge with* the defaults rather than replacing them — both
# trees are walked. Dedup is by resolved absolute path so a custom path
# that points at the default location doesn't double-emit.


def _write_plugin_json(install_path: Path, body: dict) -> None:
    cp_dir = install_path / ".claude-plugin"
    cp_dir.mkdir(exist_ok=True)
    (cp_dir / "plugin.json").write_text(
        json.dumps({"name": "superpowers", "version": "5.1.0", **body})
    )


def test_install_walks_custom_commands_path_from_plugin_json(tmp_path):
    """`plugin.json["commands"]: "./tools/cmds/"` walks the custom dir too."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    custom_dir = install_path / "tools" / "cmds"
    custom_dir.mkdir(parents=True)
    (custom_dir / "deploy.md").write_text("body\n")
    _write_plugin_json(install_path, {"commands": "./tools/cmds/"})
    refs, _ = parse_install(install_root=tmp_path)
    cmd_refs = [r for r in refs if r.extra.get("component_type") == "command"]
    assert any(r.component_identity == "claude-command/superpowers/deploy" for r in cmd_refs)


def test_install_walks_default_and_custom_commands_paths_together(tmp_path):
    """Defaults merge with custom paths — both trees walked. Dedup by resolved
    path means a plugin pointing custom=default doesn't double-emit."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    (install_path / "commands").mkdir()
    (install_path / "commands" / "deploy.md").write_text("body\n")
    custom_dir = install_path / "tools" / "cmds"
    custom_dir.mkdir(parents=True)
    (custom_dir / "release.md").write_text("body\n")
    _write_plugin_json(install_path, {"commands": "./tools/cmds/"})
    refs, _ = parse_install(install_root=tmp_path)
    cmd_names = sorted(r.name or "" for r in refs if r.extra.get("component_type") == "command")
    assert cmd_names == ["deploy", "release"]


def test_install_dedupes_custom_commands_equal_to_default(tmp_path):
    """Plugin declaring `commands: "./commands/"` (same as default) → no dup."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    (install_path / "commands").mkdir()
    (install_path / "commands" / "deploy.md").write_text("body\n")
    _write_plugin_json(install_path, {"commands": "./commands/"})
    refs, _ = parse_install(install_root=tmp_path)
    cmd_refs = [r for r in refs if r.extra.get("component_type") == "command"]
    assert len(cmd_refs) == 1


def test_install_walks_custom_agents_path_from_plugin_json(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    custom_dir = install_path / "tools" / "ag"
    custom_dir.mkdir(parents=True)
    (custom_dir / "reviewer.md").write_text("body\n")
    _write_plugin_json(install_path, {"agents": "./tools/ag/"})
    refs, _ = parse_install(install_root=tmp_path)
    agent_refs = [r for r in refs if r.extra.get("component_type") == "agent"]
    assert any(r.component_identity == "claude-agent/superpowers/reviewer" for r in agent_refs)


def test_install_walks_custom_skills_path_from_plugin_json(tmp_path):
    """Supabase-shape: `"skills": "./skills/"`. Custom + default resolve to the
    same directory and dedup keeps it to one walk."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    skills_dir = install_path / "skills" / "bootstrap"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: bootstrap\ndescription: scaffold a project\n---\nbody\n"
    )
    _write_plugin_json(install_path, {"skills": "./skills/"})
    refs, _ = parse_install(install_root=tmp_path)
    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert len(skill_refs) == 1
    assert skill_refs[0].name == "bootstrap"


def test_install_walks_custom_skills_path_distinct_from_default(tmp_path):
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    # Default
    (install_path / "skills" / "alpha").mkdir(parents=True)
    (install_path / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: x\n---\n"
    )
    # Custom location distinct from default
    custom_root = install_path / "extras" / "skills"
    (custom_root / "beta").mkdir(parents=True)
    (custom_root / "beta" / "SKILL.md").write_text("---\nname: beta\ndescription: y\n---\n")
    _write_plugin_json(install_path, {"skills": "./extras/skills/"})
    refs, _ = parse_install(install_root=tmp_path)
    skill_names = sorted(r.name or "" for r in refs if r.extra.get("component_type") == "skill")
    assert skill_names == ["alpha", "beta"]


def test_install_rejects_default_skills_dir_symlink_outside_plugin_root(tmp_path):
    import os

    external_skills = tmp_path / "external-skills"
    external_skill = external_skills / "escape"
    external_skill.mkdir(parents=True)
    (external_skill / "SKILL.md").write_text(
        "---\nname: escape\ndescription: escaped skill\n---\nbody\n"
    )
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    _write_plugin_json(install_path, {})
    os.symlink(external_skills, install_path / "skills")

    refs, _ = parse_install(install_root=tmp_path)

    skill_refs = [r for r in refs if r.extra.get("component_type") == "skill"]
    assert skill_refs == []


def test_install_walks_string_path_hooks_from_plugin_json(tmp_path):
    """`plugin.json["hooks"]: "./custom-hooks.json"` (string form) is walked
    as a hooks.json file with the same plugin-scope identity."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    custom_hooks = install_path / "custom-hooks.json"
    custom_hooks.write_text(
        json.dumps(
            {
                "description": "custom",
                "hooks": {"PreToolUse": [{"type": "command", "command": "echo pre"}]},
            }
        )
    )
    _write_plugin_json(install_path, {"hooks": "./custom-hooks.json"})
    refs, _ = parse_install(install_root=tmp_path)
    hook_refs = [r for r in refs if r.extra.get("component_type") == "hook"]
    assert len(hook_refs) == 1
    assert (hook_refs[0].component_identity or "").startswith("claude-hook/command:")
    assert hook_refs[0].extra["event"] == "PreToolUse"


def test_install_string_path_hooks_dedupes_against_default(tmp_path):
    """Default hooks/hooks.json AND custom string-path pointing at the same
    file → walked once, not twice."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    hooks_dir = install_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "description": "default",
                "hooks": {"PreToolUse": [{"type": "command", "command": "echo pre"}]},
            }
        )
    )
    _write_plugin_json(install_path, {"hooks": "./hooks/hooks.json"})
    refs, _ = parse_install(install_root=tmp_path)
    hook_refs = [r for r in refs if r.extra.get("component_type") == "hook"]
    assert len(hook_refs) == 1


def test_install_rejects_custom_path_traversal(tmp_path):
    """A plugin.json that tries to escape the install root via `..` must be
    rejected — the walker never descends outside the plugin's tree."""
    install_path = _build_install_with_plugin(
        tmp_path, plugin_key="superpowers@m", plugin_name="superpowers", version="5.1.0"
    )
    # Create a sibling dir outside install_path with a stray command file.
    sibling = install_path.parent / "outside"
    sibling.mkdir()
    (sibling / "evil.md").write_text("body\n")
    _write_plugin_json(install_path, {"commands": "../outside/"})
    refs, _ = parse_install(install_root=tmp_path)
    cmd_refs = [r for r in refs if r.extra.get("component_type") == "command"]
    assert cmd_refs == []


def _write_skill(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill\n---\n\n# {name}\n"
    )


def test_direct_symlinked_skill_uses_global_skill_lock_source_provenance(tmp_path):
    install_root = tmp_path / "claude"
    canonical_skill = tmp_path / ".agents" / "skills" / "aws-api"
    _write_skill(canonical_skill, "aws-api")
    (tmp_path / ".agents" / ".skill-lock.json").write_text(
        json.dumps(
            {
                "version": 3,
                "skills": {
                    "aws-api": {
                        "source": "awslabs/agent-toolkit-for-aws",
                        "sourceType": "github",
                        "sourceUrl": "https://github.com/awslabs/agent-toolkit-for-aws.git",
                        "ref": "main",
                        "skillPath": "skills/aws-api/SKILL.md",
                        "skillFolderHash": "1234567890abcdef",
                    }
                },
            }
        )
    )
    (install_root / "skills").mkdir(parents=True)
    (install_root / "skills" / "aws-api").symlink_to(canonical_skill, target_is_directory=True)

    refs, warnings = parse_install(install_root=install_root)

    assert warnings == []
    skill = next(r for r in refs if r.component_identity == "skill/aws-api")
    assert skill.source_manifest == str(install_root / "skills" / "aws-api" / "SKILL.md")
    assert skill.extra["source_provenance"] == {
        "status": "known",
        "source": "awslabs/agent-toolkit-for-aws",
        "source_type": "github",
        "source_url": "https://github.com/awslabs/agent-toolkit-for-aws.git",
        "ref": "main",
        "skill_path": "skills/aws-api/SKILL.md",
        "hash": "1234567890abcdef",
        "hash_type": "skillFolderHash",
        "lockfile_path": str(tmp_path / ".agents" / ".skill-lock.json"),
        "resolved_path": str(canonical_skill / "SKILL.md"),
    }


def test_direct_symlinked_skill_without_lock_entry_records_target(tmp_path):
    install_root = tmp_path / "claude"
    canonical_skill = tmp_path / ".agents" / "skills" / "loose-skill"
    _write_skill(canonical_skill, "loose-skill")
    (install_root / "skills").mkdir(parents=True)
    (install_root / "skills" / "loose-skill").symlink_to(canonical_skill, target_is_directory=True)

    refs, warnings = parse_install(install_root=install_root)

    assert warnings == []
    skill = next(r for r in refs if r.component_identity == "skill/loose-skill")
    assert skill.extra["source_provenance"] == {
        "status": "symlink-target",
        "resolved_path": str(canonical_skill / "SKILL.md"),
    }


def test_direct_copied_skill_without_lock_entry_keeps_source_unknown(tmp_path):
    install_root = tmp_path / "claude"
    _write_skill(install_root / "skills" / "copied-skill", "copied-skill")

    refs, warnings = parse_install(install_root=install_root)

    assert warnings == []
    skill = next(r for r in refs if r.component_identity == "skill/copied-skill")
    assert "source_provenance" not in skill.extra


def test_project_symlinked_skill_uses_project_skills_lock(tmp_path):
    install_root = tmp_path / "claude"
    project_root = tmp_path / "repo"
    canonical_skill = project_root / ".agents" / "skills" / "bootstrap"
    _write_skill(canonical_skill, "bootstrap")
    (project_root / "skills-lock.json").write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "bootstrap": {
                        "source": "vercel-labs/agent-skills",
                        "sourceType": "github",
                        "ref": "main",
                        "skillPath": "skills/bootstrap/SKILL.md",
                        "computedHash": "abcdef1234567890",
                    }
                },
            }
        )
    )
    (project_root / ".claude" / "skills").mkdir(parents=True)
    (project_root / ".claude" / "skills" / "bootstrap").symlink_to(
        canonical_skill, target_is_directory=True
    )

    refs, warnings = parse_install(install_root=install_root, project_root=project_root)

    assert warnings == []
    skill = next(r for r in refs if r.component_identity == "skill/bootstrap")
    assert skill.source_manifest == str(
        project_root / ".claude" / "skills" / "bootstrap" / "SKILL.md"
    )
    assert skill.extra["source_provenance"]["source"] == "vercel-labs/agent-skills"
    assert skill.extra["source_provenance"]["hash_type"] == "computedHash"
