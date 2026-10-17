"""Native user and project-local MCP state is not a settings.json declaration."""

import json
from pathlib import Path

import pytest

from tools.parsers.claude_install import global_mcp_refs


def test_scopes_and_project_key_are_preserved_without_reading_other_projects(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"user": {"url": "https://user.test/mcp"}},
                "projects": {
                    str(project): {
                        "mcpServers": {"local": {"command": "node", "args": ["local.js"]}}
                    },
                    "/other": {"mcpServers": {"excluded": {"url": "https://other.test"}}},
                },
            }
        )
    )
    refs = global_mcp_refs(root, project)
    assert {(r.extra["component_path"][-1]["name"], r.extra["claude_mcp_scope"]) for r in refs} == {
        ("user", "user"),
        ("local", "local"),
    }
    assert refs[1].source_locator.startswith("$.projects[")


def test_malformed_selected_native_state_fails_instead_of_becoming_clean(tmp_path):
    (tmp_path / ".claude.json").write_text('{"mcpServers": []}')
    with pytest.raises(ValueError):
        global_mcp_refs(tmp_path, None)


def test_default_install_reads_both_native_global_config_locations(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = tmp_path / ".claude"
    root.mkdir()
    for path in (tmp_path / ".claude.json", root / ".claude.json"):
        path.write_text('{"mcpServers":{"server":{"url":"https://example.test"}}}')
    assert {r.source_manifest for r in global_mcp_refs(root, None)} == {
        str(tmp_path / ".claude.json"),
        str(root / ".claude.json"),
    }
