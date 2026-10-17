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


def test_a_project_disabled_server_is_excluded_from_that_projects_user_scope(tmp_path):
    """Claude Code's per-project `/mcp` disable toggle records an opt-out in
    `projects[<project>].disabledMcpServers` without removing the server's
    user-scoped definition. A project that disabled it must not see it as
    active evidence, but the definition must still apply to other projects
    that did not disable it."""
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "kept": {"url": "https://kept.test"},
                    "disabled-here": {"url": "https://disabled.test"},
                },
                "projects": {
                    str(project): {"disabledMcpServers": ["disabled-here"]},
                    str(other_project): {},
                },
            }
        )
    )
    names = {r.extra["component_path"][-1]["name"] for r in global_mcp_refs(root, project)}
    assert names == {"kept"}
    other_names = {
        r.extra["component_path"][-1]["name"] for r in global_mcp_refs(root, other_project)
    }
    assert other_names == {"kept", "disabled-here"}


def test_malformed_disabled_mcp_servers_fails_instead_of_becoming_clean(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"server": {"url": "https://example.test"}},
                "projects": {str(project): {"disabledMcpServers": "not-a-list"}},
            }
        )
    )
    with pytest.raises(ValueError):
        global_mcp_refs(root, project)


def test_malformed_selected_native_state_fails_instead_of_becoming_clean(tmp_path):
    (tmp_path / ".claude.json").write_text('{"mcpServers": []}')
    with pytest.raises(ValueError):
        global_mcp_refs(tmp_path, None)


def test_default_install_selects_the_native_global_config_location(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = tmp_path / ".claude"
    root.mkdir()
    for path in (tmp_path / ".claude.json", root / ".claude.json"):
        path.write_text('{"mcpServers":{"server":{"url":"https://example.test"}}}')
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert {r.source_manifest for r in global_mcp_refs(root, None)} == {
        str(tmp_path / ".claude.json")
    }
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    (tmp_path / ".claude.json").write_text("invalid unused file")
    assert {r.source_manifest for r in global_mcp_refs(root, None)} == {str(root / ".claude.json")}


def test_an_explicit_config_dir_matching_the_default_path_still_uses_its_own_file(
    tmp_path, monkeypatch
):
    """A named `--config-dir` must fully specify the scan (ADR-0054), even when
    it happens to equal the default `~/.claude` path: `install_root ==
    ~/.claude` and `CLAUDE_CONFIG_DIR` unset are not enough on their own to
    prove this is the unoverridden default-resolution path, since an explicit
    `--kind claude-code --config-dir ~/.claude` looks identical on those two
    signals alone."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    root = tmp_path / ".claude"
    root.mkdir()
    (tmp_path / ".claude.json").write_text('{"mcpServers":{"host":{"url":"https://host.test"}}}')
    (root / ".claude.json").write_text(
        '{"mcpServers":{"explicit":{"url":"https://explicit.test"}}}'
    )

    assert {r.source_manifest for r in global_mcp_refs(root, None)} == {
        str(tmp_path / ".claude.json")
    }
    assert {
        r.source_manifest for r in global_mcp_refs(root, None, config_root_overridden=True)
    } == {str(root / ".claude.json")}
