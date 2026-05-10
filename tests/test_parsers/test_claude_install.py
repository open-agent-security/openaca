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

    plugin_refs = [r for r in refs if r.ecosystem == "claude-plugin"]
    assert len(plugin_refs) == 1
    ref = plugin_refs[0]
    assert ref.name == "sample-plugin"
    assert ref.version == "1.2.0"
    assert ref.component_identity == "claude-plugin/sample-plugin@1.2.0"
    assert ref.attributed_to is None  # plugin itself is direct
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
    asve-scan fs."""
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
    assert refs[0].component_identity == "claude-plugin/@acme/tool@1.0"


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
