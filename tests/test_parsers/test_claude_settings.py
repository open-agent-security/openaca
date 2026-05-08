from pathlib import Path

from tools.parsers.claude_settings import parse

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_enabled_plugins_emitted():
    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    identities = {r.component_identity for r in refs}
    assert "claude-plugin/deployment-tools@1.2.0" in identities
    assert "claude-plugin/anthropics/dev-tools@2.0.1" in identities
    assert not any("experimental" in (r.component_identity or "") for r in refs)


def test_source_locator():
    manifest = REPOS / "sample-settings" / ".claude" / "settings.json"
    refs = parse(manifest)
    locators = {r.source_locator for r in refs}
    assert any("$.enabledPlugins" in loc for loc in locators)
