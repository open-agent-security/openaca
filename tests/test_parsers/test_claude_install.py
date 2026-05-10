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
