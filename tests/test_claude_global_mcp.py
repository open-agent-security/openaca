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
    local_ref = refs[1]
    assert local_ref.source_locator.startswith("$.projects[")


def test_local_scope_locator_is_stable_across_machines_but_keeps_the_real_project_key(tmp_path):
    """The real project key is an absolute path; it must not leak into
    `source_locator` (identity — feeds the occurrence key/bom-ref unnormalized)
    while still being recoverable as evidence."""
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    str(project): {"mcpServers": {"local": {"url": "https://local.test"}}},
                },
            }
        )
    )
    (local_ref,) = global_mcp_refs(root, project)
    assert str(project.resolve()) not in local_ref.source_locator
    assert local_ref.extra["claude_mcp_project_key"] == str(project.resolve())


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
