import json
from pathlib import Path

from tools.parsers.claude_settings import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_enabled_plugins_emitted():
    """Each enabled plugin emits a source-less plugin component ref. Identity
    includes marketplace when present so same-name plugins from different
    marketplaces do not collide."""
    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    assert all(r.extra.get("component_type") == "plugin" for r in refs)
    names = {r.name for r in refs}
    # Fixture uses `<name>@<marketplace>` form; rsplit keeps the part before
    # the last @.
    assert "deployment-tools" in names
    assert "anthropics/dev-tools" in names
    assert "experimental" not in names  # is_enabled was False
    identities = {r.component_identity for r in refs}
    assert "plugin/test-market/deployment-tools" in identities
    assert "plugin/test-market/anthropics/dev-tools" in identities
    assert all(r.version is None for r in refs)


def test_settings_plugin_matches_component_identity_advisory():
    """Enabled plugins are source-less components, so advisories target their
    logical component identity rather than a component-type ecosystem."""
    from tools.matcher import match

    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    advisory = {
        "id": "OpenACA-TEST-PLUGIN-1",
        "database_specific": {
            "openaca": {"component_identity": "plugin/test-market/deployment-tools"}
        },
    }
    findings = match(refs, [advisory])
    matching = [f for f in findings if f.advisory_id == "OpenACA-TEST-PLUGIN-1"]
    assert matching
    assert matching[0].confidence == "high"


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
    assert refs[0].component_identity == "plugin/bare-name"
    assert refs[0].extra["marketplace"] is None


def test_settings_same_plugin_name_different_marketplaces_have_distinct_identities(tmp_path):
    manifest = tmp_path / "settings.json"
    manifest.write_text(
        json.dumps(
            {
                "enabledPlugins": {
                    "shared@market-one": True,
                    "shared@market-two": True,
                }
            }
        )
    )
    refs = parse(manifest)
    assert {r.component_identity for r in refs} == {
        "plugin/market-one/shared",
        "plugin/market-two/shared",
    }


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
