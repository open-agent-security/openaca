"""claude_desktop_config.json shares the mcp.json shape — registry test only.

The content parser (`mcp_json.parse`) is unit-tested in test_mcp_json.py;
here we only verify that the new filename is registered and exercised by
parse_repo against a fixture file with the canonical name.
"""

from pathlib import Path

from tools.parsers import parse_repo

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_parse_repo_picks_up_claude_desktop_config():
    refs = parse_repo(REPOS / "sample-claude-desktop")
    purls = {r.purl for r in refs if r.purl}
    assert "pkg:npm/%40cyanheads/git-mcp-server@1.1.0" in purls
    assert "pkg:pypi/weather-mcp@0.5.0" in purls


def test_parse_repo_records_source_manifest_filename():
    refs = parse_repo(REPOS / "sample-claude-desktop")
    sources = {r.source_manifest for r in refs}
    assert any(s.endswith("claude_desktop_config.json") for s in sources)
