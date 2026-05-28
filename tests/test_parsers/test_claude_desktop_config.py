"""claude_desktop_config.json shares the mcp.json shape.

The content parser (`mcp_json.parse`) is unit-tested in test_mcp_json.py;
these tests cover Desktop-specific registry and runtime-host behavior.
"""

import json
from pathlib import Path

from tools.parsers import mcp_json, parse_repo

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


def test_parse_repo_stamps_claude_desktop_config_as_claude_chat():
    refs = parse_repo(REPOS / "sample-claude-desktop")
    hosts = {tuple(r.extra.get("runtime_hosts") or []) for r in refs}
    assert hosts == {("claude-chat",)}


def test_parse_with_runtime_hosts_stamps_claude_chat(tmp_path):
    manifest = tmp_path / "claude_desktop_config.json"
    manifest.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "inspector": {
                        "command": "npx",
                        "args": ["@mcpjam/inspector@1.4.2"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    refs = mcp_json.parse_with_runtime_hosts(manifest, ["claude-chat"])

    assert len(refs) == 1
    assert refs[0].purl == "pkg:npm/%40mcpjam/inspector@1.4.2"
    assert refs[0].extra["runtime_hosts"] == ["claude-chat"]
