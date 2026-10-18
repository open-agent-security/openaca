"""Native user and project-local MCP state is not a settings.json declaration."""

import json
from pathlib import Path

import pytest

from tools.parsers.claude_install import effective_mcp_refs


def test_scopes_and_project_key_are_preserved_without_reading_other_projects(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"user": {"type": "http", "url": "https://user.test/mcp"}},
                "projects": {
                    str(project): {
                        "mcpServers": {"local": {"command": "node", "args": ["local.js"]}}
                    },
                    "/other": {
                        "mcpServers": {"excluded": {"type": "http", "url": "https://other.test"}}
                    },
                },
            }
        )
    )
    refs = effective_mcp_refs(root, project)
    assert {(r.extra["component_path"][-1]["name"], r.extra["claude_mcp_scope"]) for r in refs} == {
        ("user", "user"),
        ("local", "local"),
    }
    local_ref = next(ref for ref in refs if ref.extra["claude_mcp_scope"] == "local")
    assert local_ref.source_locator.startswith("$.projects[")


def test_a_name_defined_in_both_user_and_local_scope_emits_only_the_local_entry(tmp_path):
    """Claude Code connects to a same-named server once, using the entry from
    the highest-precedence scope (local > project > user); it never merges
    or runs both. The user-scope entry a local one shadows must not emit as
    a second, dormant active ref."""
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"type": "http", "url": "https://user-shadowed.test"},
                    "user-only": {"type": "http", "url": "https://user-only.test"},
                },
                "projects": {
                    str(project): {
                        "mcpServers": {"shared": {"type": "http", "url": "https://local-wins.test"}}
                    },
                },
            }
        )
    )
    refs = effective_mcp_refs(root, project)
    by_name = {r.extra["component_path"][-1]["name"]: r for r in refs}
    assert set(by_name) == {"shared", "user-only"}
    assert by_name["shared"].extra["claude_mcp_scope"] == "local"
    assert by_name["shared"].extra["url"] == "https://local-wins.test"


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
                    str(project): {
                        "mcpServers": {"local": {"type": "http", "url": "https://local.test"}}
                    },
                },
            }
        )
    )
    (local_ref,) = effective_mcp_refs(root, project)
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
                    "kept": {"type": "http", "url": "https://kept.test"},
                    "disabled-here": {"type": "http", "url": "https://disabled.test"},
                },
                "projects": {
                    str(project): {"disabledMcpServers": ["disabled-here"]},
                    str(other_project): {},
                },
            }
        )
    )
    names = {r.extra["component_path"][-1]["name"] for r in effective_mcp_refs(root, project)}
    assert names == {"kept"}
    other_names = {
        r.extra["component_path"][-1]["name"] for r in effective_mcp_refs(root, other_project)
    }
    assert other_names == {"kept", "disabled-here"}


def test_a_project_disabled_server_is_excluded_from_its_own_local_scope_too(tmp_path):
    """`disabledMcpServers` disables a server by name regardless of which
    scope defines it — including the project's own local-scope entry, not
    only an inherited user-scoped one."""
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    str(project): {
                        "mcpServers": {
                            "kept-local": {"type": "http", "url": "https://kept-local.test"},
                            "disabled-local": {
                                "type": "http",
                                "url": "https://disabled-local.test",
                            },
                        },
                        "disabledMcpServers": ["disabled-local"],
                    },
                },
            }
        )
    )
    names = {r.extra["component_path"][-1]["name"] for r in effective_mcp_refs(root, project)}
    assert names == {"kept-local"}


def test_disabled_mcp_servers_does_not_affect_the_project_scope(tmp_path):
    """`disabledMcpServers` and the project `.mcp.json` approval lists
    (`disabledMcpjsonServers`/`enabledMcpjsonServers`) cover disjoint server
    sets. Listing a name in `disabledMcpServers` must not drop that name's
    entry in the project's own `.mcp.json` — an active, higher-precedence
    project server would otherwise disappear from evidence for no reason
    Claude Code itself recognizes."""
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps({"projects": {str(project): {"disabledMcpServers": ["shared"]}}})
    )
    (project / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"shared": {"type": "http", "url": "https://project.test/mcp"}}})
    )

    (ref,) = effective_mcp_refs(root, project)

    assert ref.extra["claude_mcp_scope"] == "project"
    assert ref.extra["url"] == "https://project.test/mcp"


def test_a_valid_entry_the_generic_parser_cannot_represent_still_blocks_fallthrough(
    tmp_path,
):
    """`npx -c '<cmd>'` is valid per npm's own docs, but the generic,
    kind-neutral parser's npx branch has no fallback for it and silently
    returns no ref — not a ValueError, just an empty parse. A valid entry
    must reserve its name as soon as it is seen, before the generic parser
    ever runs, or this failure mode (parse succeeds with zero refs, not an
    exception) would let a same-named lower-scope entry be selected in its
    place exactly like an invalid one would."""
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"type": "http", "url": "https://user-shadowed.test"},
                },
                "projects": {
                    str(project): {
                        "mcpServers": {
                            "shared": {"command": "npx", "args": ["-c", "node server.js"]},
                        }
                    },
                },
            }
        )
    )

    refs = effective_mcp_refs(root, project)

    assert refs == []


def test_malformed_disabled_mcp_servers_fails_instead_of_becoming_clean(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"server": {"type": "http", "url": "https://example.test"}},
                "projects": {str(project): {"disabledMcpServers": "not-a-list"}},
            }
        )
    )
    with pytest.raises(ValueError):
        effective_mcp_refs(root, project)


def test_malformed_selected_native_state_fails_instead_of_becoming_clean(tmp_path):
    (tmp_path / ".claude.json").write_text('{"mcpServers": []}')
    with pytest.raises(ValueError):
        effective_mcp_refs(tmp_path, None)


def test_default_install_selects_the_native_global_config_location(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    root = tmp_path / ".claude"
    root.mkdir()
    for path in (tmp_path / ".claude.json", root / ".claude.json"):
        path.write_text('{"mcpServers":{"server":{"type": "http", "url":"https://example.test"}}}')
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert {r.source_manifest for r in effective_mcp_refs(root, None)} == {
        str(tmp_path / ".claude.json")
    }
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    (tmp_path / ".claude.json").write_text("invalid unused file")
    assert {r.source_manifest for r in effective_mcp_refs(root, None)} == {
        str(root / ".claude.json")
    }


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
    (tmp_path / ".claude.json").write_text(
        '{"mcpServers":{"host":{"type": "http", "url":"https://host.test"}}}'
    )
    (root / ".claude.json").write_text(
        '{"mcpServers":{"explicit":{"type": "http", "url":"https://explicit.test"}}}'
    )

    assert {r.source_manifest for r in effective_mcp_refs(root, None)} == {
        str(tmp_path / ".claude.json")
    }
    assert {
        r.source_manifest for r in effective_mcp_refs(root, None, config_root_overridden=True)
    } == {str(root / ".claude.json")}


def test_an_interpolated_local_url_blocks_the_shadowed_user_entry_instead_of_falling_through(
    tmp_path,
):
    """Claude Code expands `${VAR}` in a url at load time and activates the
    entry regardless — even an unresolved reference loads with the literal
    text, per Claude Code's own docs. The local entry is real and active;
    this parser just can't normalize its url without that resolution. That
    must not read as "invalid, fall through to the next scope": selecting
    the shadowed user-scope entry instead would report policy/posture
    findings against an endpoint the project never actually connects to."""
    root = tmp_path / "config"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (root / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared": {"type": "http", "url": "https://user-shadowed.test"},
                },
                "projects": {
                    str(project): {
                        "mcpServers": {
                            "shared": {"type": "http", "url": "${MCP_URL}/mcp"},
                        }
                    },
                },
            }
        )
    )

    refs = effective_mcp_refs(root, project)

    assert refs == []


@pytest.mark.parametrize(
    "entry,selected",
    [
        ({"url": "https://example.test/mcp"}, False),
        ({"type": "stdio", "url": "https://example.test/mcp"}, False),
        ({"type": "sdk", "url": "https://example.test/mcp"}, False),
        ({"type": "http"}, False),
        ({"command": "node", "args": "not-an-array"}, False),
        ({"command": "node", "args": ["server.js"]}, True),
        ({"type": "stdio", "command": "node", "args": ["server.js"]}, True),
        ({"type": "http", "url": "https://example.test/mcp"}, True),
        ({"type": "streamable-http", "url": "https://example.test/mcp"}, True),
        ({"type": "sse", "url": "https://example.test/mcp"}, True),
        ({"type": "ws", "url": "wss://example.test/mcp"}, True),
    ],
)
def test_native_transport_requirements_do_not_use_the_generic_url_default(
    tmp_path, entry, selected
):
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"test": entry}}))
    assert bool(effective_mcp_refs(tmp_path, None)) is selected
