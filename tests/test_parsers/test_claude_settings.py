from pathlib import Path

from tools.parsers.claude_settings import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_enabled_plugins_emitted():
    """Each enabled plugin emits a ref with ecosystem='claude-plugin' and
    name=<plugin-name>, so the matcher's _match_versioned path fires against
    claude-plugin advisories (ADR-0006). Identity drops the @<marketplace>
    suffix from the settings key — settings doesn't carry plugin versions,
    only declaration intent."""
    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    assert all(r.ecosystem == "claude-plugin" for r in refs)
    names = {r.name for r in refs}
    # Fixture uses `<name>@<marketplace>` form; rsplit keeps the part before
    # the last @.
    assert "deployment-tools" in names
    assert "anthropics/dev-tools" in names
    assert "experimental" not in names  # is_enabled was False
    identities = {r.component_identity for r in refs}
    assert "claude-plugin/deployment-tools" in identities
    assert "claude-plugin/anthropics/dev-tools" in identities
    assert all(r.version is None for r in refs)


def test_settings_plugin_matches_claude_plugin_advisory_by_name():
    """A repo declaring `enabledPlugins: {"deployment-tools@market": true}`
    should match an advisory targeting `(ecosystem=claude-plugin,
    name=deployment-tools)` via the matcher's versioned path. Version is
    unknown from settings alone, so confidence is 'low' (pin-to-verify),
    NOT zero findings."""
    from tools.matcher import match

    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    advisory = {
        "id": "ASVE-TEST-PLUGIN-1",
        "affected": [
            {
                "package": {"ecosystem": "claude-plugin", "name": "deployment-tools"},
                "ranges": [
                    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "9.0.0"}]}
                ],
            }
        ],
    }
    findings = match(refs, [advisory])
    matching = [f for f in findings if f.advisory_id == "ASVE-TEST-PLUGIN-1"]
    assert matching
    assert matching[0].confidence == "low"


def test_settings_plugin_non_true_values_are_disabled(tmp_path):
    """Truthy non-True values (string `'true'`, int `1`, dict `{}`) must
    NOT enable a plugin — only literal JSON true."""
    manifest = tmp_path / "settings.json"
    manifest.write_text('{"enabledPlugins": {"a@m": "true", "b@m": 1, "c@m": {}, "d@m": true}}')
    refs = parse(manifest)
    names = {r.name for r in refs}
    assert names == {"d"}


def test_settings_plugin_unscoped_name(tmp_path):
    """Bare plugin name (no @marketplace) emits with name=<spec>."""
    manifest = tmp_path / "settings.json"
    manifest.write_text('{"enabledPlugins": {"bare-name": true}}')
    refs = parse(manifest)
    assert len(refs) == 1
    assert refs[0].name == "bare-name"
    assert refs[0].component_identity == "claude-plugin/bare-name"


def test_source_locator():
    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    locators = {r.source_locator for r in refs}
    assert any("$.enabledPlugins" in loc for loc in locators)


def test_enabled_plugins_as_list_does_not_raise(tmp_path):
    """Malformed enabledPlugins: [...] should return [] not raise AttributeError."""
    manifest = tmp_path / "settings.json"
    manifest.write_text('{"enabledPlugins": ["foo-plugin"]}')
    assert parse(manifest) == []


def test_top_level_array_does_not_raise(tmp_path):
    """Settings file whose root is `[]` (not an object) must not crash."""
    manifest = tmp_path / "settings.json"
    manifest.write_text("[]")
    assert parse(manifest) == []


def test_top_level_null_does_not_raise(tmp_path):
    manifest = tmp_path / "settings.json"
    manifest.write_text("null")
    assert parse(manifest) == []
