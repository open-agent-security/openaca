"""Tests for Cursor's posture surfaces (task-7 brief,
`.superpowers/sdd/042-cursor-agent-kind/task-7-brief.md`): the declared/
installed MCP-shaped collectors, the JSONC `permissions.json` merge, and the
`mcp_auto_approve` branch that reads it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tools.component_ref import ComponentRef
from tools.graph_build_cursor import build_cursor_graph
from tools.posture import (
    collect_cursor_endpoint_mcp_manifests,
    collect_cursor_endpoint_permissions_manifests,
    collect_cursor_mcp_manifests,
    collect_cursor_permissions_manifests,
    no_manifests,
    resolve_cursor_permissions,
)
from tools.posture.rules.mcp_auto_approve import check_mcp_auto_approve
from tools.remote.collector import _agent_posture_manifests as _remote_agent_posture_manifests
from tools.scan import _agent_scan_prep as _scan_agent_scan_prep


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- no_manifests consolidation --------------------------------------------


@dataclass
class _FakeAgent:
    """Minimal `AgentInstance` stand-in — `build_cursor_graph` reads only these."""

    source: str
    scan_root: Optional[Path] = None
    config_root: Optional[Path] = None
    project_root: Optional[Path] = None
    bom_ref: str = "root/cursor"
    root_label: str = "cursor"


def _declared_refs(repo_root: Path, *, include_gitignored: bool = False) -> list[ComponentRef]:
    """Compose the declared Cursor graph and return its refs.

    Posture derives from these rather than re-walking (see
    `collect_cursor_mcp_manifests`), so a posture test that builds the graph
    first is asserting the property that actually matters: posture reports
    exactly what composition composed, never more.
    """
    graph = build_cursor_graph(
        _FakeAgent(source="declared", scan_root=repo_root),
        include_gitignored=include_gitignored,
    )
    return [n.ref for n in graph.nodes.values() if n.ref is not None]


def test_no_manifests_returns_empty_regardless_of_args():
    assert no_manifests() == []
    assert no_manifests(1, 2, three=3) == []


def test_scan_and_collector_import_the_shared_no_manifests():
    import tools.remote.collector as collector
    import tools.scan as scan

    assert scan.no_manifests is no_manifests
    assert collector.no_manifests is no_manifests
    assert not hasattr(scan, "_no_manifests")
    assert not hasattr(collector, "_no_manifests")


# --- Declared MCP collector: scoped mcp.json + plugin candidate list ------


def test_declared_collects_scoped_cursor_mcp_json(tmp_path):
    _write(
        tmp_path / ".cursor" / "mcp.json",
        {"mcpServers": {"unsafe": {"url": "http://example.com/mcp"}}},
    )

    manifests = collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path))

    assert len(manifests) == 1
    path, data = manifests[0]
    assert path == tmp_path / ".cursor" / "mcp.json"
    assert data["mcpServers"]["unsafe"]["url"] == "http://example.com/mcp"


def test_declared_bare_mcp_json_not_matched_by_scoped_surface(tmp_path):
    """`mcp.json` outside a `.cursor/` dir is not Cursor's scoped surface."""
    _write(tmp_path / "mcp.json", {"mcpServers": {"x": {"url": "http://x.example/mcp"}}})

    assert collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path)) == []


def test_declared_bundled_mcp_reached_via_claude_plugin_manifest_only(tmp_path):
    """A plugin bundled only as `.claude-plugin/plugin.json` (no native
    `.cursor-plugin`) is still a manifest Cursor reads, so its bundled MCP
    server must reach posture the same way it reaches composition — via the
    same ordered candidate list, not a shorter hand-rolled one."""
    root = tmp_path / "plug"
    _write(root / ".claude-plugin" / "plugin.json", {"name": "plug"})
    _write(root / "mcp.json", {"mcpServers": {"insecure": {"url": "http://bad.example/mcp"}}})

    manifests = collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path))

    assert len(manifests) == 1
    path, data = manifests[0]
    assert path == root / "mcp.json"
    assert data["mcpServers"]["insecure"]["url"] == "http://bad.example/mcp"


def test_declared_native_plugin_inline_mcp_servers_collected(tmp_path):
    """`claude_plugin_root._parse_manifest_refs` reads a top-level
    `mcpServers` OBJECT straight off the plugin manifest itself
    ("$.mcpServers (inlined)") — composition never needs a sibling
    `mcp.json` for this case. Posture must inspect the manifest the same
    way, or an inline insecure-transport server is invisible to a declared
    scan even though composition loaded it."""
    root = tmp_path / "plug"
    _write(
        root / ".claude-plugin" / "plugin.json",
        {"name": "plug", "mcpServers": {"inline": {"url": "http://inline.example/mcp"}}},
    )

    manifests = collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path))

    assert len(manifests) == 1
    path, data = manifests[0]
    assert path == root / ".claude-plugin" / "plugin.json"
    assert data["mcpServers"]["inline"]["url"] == "http://inline.example/mcp"


def test_declared_native_plugin_referenced_mcp_path_collected(tmp_path):
    """`mcpServers` may instead be a STRING: a path to a separate MCP
    manifest resolved relative to the plugin root, under any filename — not
    just `mcp.json`/`.mcp.json`. Composition (`_parse_manifest_refs`) follows
    it; posture must follow the same reference or miss the servers it
    contains."""
    root = tmp_path / "plug"
    _write(
        root / ".claude-plugin" / "plugin.json",
        {"name": "plug", "mcpServers": "custom/servers.json"},
    )
    _write(
        root / "custom" / "servers.json",
        {"mcpServers": {"referenced": {"url": "http://referenced.example/mcp"}}},
    )

    manifests = collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path))

    assert len(manifests) == 1
    path, data = manifests[0]
    assert path == root / "custom" / "servers.json"
    assert data["mcpServers"]["referenced"]["url"] == "http://referenced.example/mcp"


def test_declared_native_plugin_missing_name_yields_no_mcp(tmp_path):
    """A native manifest that satisfies `fmt.detect` (so it appears among
    `find_plugin_roots`' candidates) but lacks a `name` never yields a plugin
    self-ref from `claude_plugin.parse` — `_descend_into_plugin` returns
    `None` and the directory is never realized into the graph. Neither its
    inline `mcpServers`, a referenced path, nor a sibling `mcp.json` should
    surface in posture: none of them are reachable from composition when the
    plugin itself was rejected."""
    root = tmp_path / "plug"
    _write(
        root / ".claude-plugin" / "plugin.json",
        {"mcpServers": {"inline": {"url": "http://inline.example/mcp"}}},
    )
    _write(root / "mcp.json", {"mcpServers": {"sibling": {"url": "http://sibling.example/mcp"}}})

    assert collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path)) == []


def test_declared_invalid_agent_plugins_manifest_yields_no_mcp(tmp_path):
    """A schema-recognized root `plugin.json` that fails `validate_manifest`
    does not win — its bundled `mcp.json` must not surface posture, exactly
    as if the plugin.json were absent."""
    root = tmp_path / "bad-agent-plugin"
    _write(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "Not A Valid Name!",
        },
    )
    _write(root / "mcp.json", {"mcpServers": {"x": {"url": "http://x.example/mcp"}}})

    assert collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path)) == []


def test_declared_valid_agent_plugins_manifest_surfaces_bundled_mcp(tmp_path):
    root = tmp_path / "good-agent-plugin"
    _write(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "good-plugin",
        },
    )
    _write(
        root / "mcp.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {"srv": {"url": "http://srv.example/mcp"}},
        },
    )

    manifests = collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path))

    assert len(manifests) == 1
    assert manifests[0][0] == root / "mcp.json"


def test_declared_agent_plugins_mcp_missing_schema_yields_no_mcp(tmp_path):
    """§7.2.1: a bundled `mcp.json` MUST carry its own `$schema` — one that
    only has `mcpServers` is invalid, exactly as composition
    (`agent_plugins._parse_mcp`) would reject it, so posture must not surface
    a server the graph never composed."""
    root = tmp_path / "schemaless-agent-plugin"
    _write(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "schemaless-plugin",
        },
    )
    _write(root / "mcp.json", {"mcpServers": {"srv": {"url": "http://srv.example/mcp"}}})

    assert collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path)) == []


def test_declared_agent_plugins_dot_mcp_json_sibling_is_ignored(tmp_path):
    """`agent_plugins._parse_mcp` only ever resolves a plugin-root `mcp.json`
    (§7.2.1) — never `.mcp.json`, which is a native Cursor-bundle filename
    with no meaning under Agent Plugins. A valid-looking `.mcp.json` sibling
    (no `mcp.json` present) must not surface posture: composition never
    loads it for this format, so reporting on it would flag servers the
    graph never composed."""
    root = tmp_path / "dotfile-agent-plugin"
    _write(
        root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "dotfile-plugin",
        },
    )
    _write(
        root / ".mcp.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {"srv": {"url": "http://srv.example/mcp"}},
        },
    )

    assert collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path)) == []


def test_declared_directory_with_both_native_and_claude_manifest_reports_once(tmp_path):
    """A directory carrying both a native and a Claude-format manifest
    reports posture for the first-winning candidate only, not shadowed
    content from both."""
    root = tmp_path / "dual"
    _write(root / ".cursor-plugin" / "plugin.json", {"name": "dual", "author": {}})
    _write(root / "mcp.json", {"mcpServers": {"native": {"url": "http://native.example/mcp"}}})
    _write(root / ".claude-plugin" / "plugin.json", {"name": "dual-claude"})

    manifests = collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path))

    assert len(manifests) == 1
    assert manifests[0][0] == root / "mcp.json"


def test_declared_honors_include_gitignored_false(tmp_path):
    (tmp_path / ".gitignore").write_text(".cursor/\n")
    _write(tmp_path / ".cursor" / "mcp.json", {"mcpServers": {"x": {"url": "http://x/mcp"}}})

    assert (
        collect_cursor_mcp_manifests(
            [tmp_path],
            include_gitignored=False,
            refs=_declared_refs(tmp_path, include_gitignored=False),
        )
        == []
    )
    assert (
        len(
            collect_cursor_mcp_manifests(
                [tmp_path],
                include_gitignored=True,
                refs=_declared_refs(tmp_path, include_gitignored=True),
            )
        )
        == 1
    )


def test_declared_bundled_mcp_matched_by_gitignore_is_excluded(tmp_path):
    """A realized plugin's bundled `mcp.json`, matched by the scan-root
    `.gitignore`, must not surface posture even though the plugin's manifest
    itself is unignored — composition (`descend_into_plugin`) filters its
    emitted refs the same way, so a default `scan repo --include-posture`
    would otherwise report insecure-transport findings for a server absent
    from the Cursor graph."""
    plugin_dir = tmp_path / "plug"
    _write(plugin_dir / ".claude-plugin" / "plugin.json", {"name": "plug"})
    _write(plugin_dir / "mcp.json", {"mcpServers": {"insecure": {"url": "http://bad.example/mcp"}}})
    _write_text(tmp_path / ".gitignore", "plug/mcp.json\n")

    assert (
        collect_cursor_mcp_manifests(
            [tmp_path],
            include_gitignored=False,
            refs=_declared_refs(tmp_path, include_gitignored=False),
        )
        == []
    )
    assert (
        len(
            collect_cursor_mcp_manifests(
                [tmp_path],
                include_gitignored=True,
                refs=_declared_refs(tmp_path, include_gitignored=True),
            )
        )
        == 1
    )


def test_declared_nested_agent_plugins_fixture_under_native_root_excluded(tmp_path):
    """An Agent Plugins bundle's own fixture content, sitting strictly below
    an already-realized native (`.cursor-plugin`) root, is never composed as
    an independent bundle (`graph_build_cursor._realize_plugins`'s nesting
    filter) — posture must not report on its bundled `mcp.json` either, or a
    scan surfaces servers from example fixtures the graph never loaded."""
    native_root = tmp_path / "native"
    _write(native_root / ".cursor-plugin" / "plugin.json", {"name": "native", "author": {}})
    nested_root = native_root / "examples" / "demo"
    _write(
        nested_root / "plugin.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "nested-demo",
        },
    )
    _write(
        nested_root / "mcp.json",
        {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {"srv": {"url": "http://srv.example/mcp"}},
        },
    )

    assert collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path)) == []


def test_declared_nested_cursor_mcp_json_inside_native_plugin_excluded(tmp_path):
    """A realized native plugin's own `.cursor/mcp.json` fixture content is
    never read by composition — `_add_scoped_mcps`'s `exclude_under` drops
    every path beneath a realized plugin root, and plugin descent itself only
    ever reads the plugin root's own bundled `mcp.json`/`.mcp.json`, never a
    nested `.cursor/mcp.json` inside it. The unrestricted top-level
    `root.rglob("mcp.json")` walk must mirror that exclusion, or posture
    reports insecure-transport findings for a server absent from the graph.
    A sibling `.cursor/mcp.json` OUTSIDE the plugin subtree is unaffected."""
    plugin_dir = tmp_path / "plug"
    _write(plugin_dir / ".claude-plugin" / "plugin.json", {"name": "plug"})
    _write(
        plugin_dir / ".cursor" / "mcp.json",
        {"mcpServers": {"fixture": {"url": "http://fixture.example/mcp"}}},
    )
    _write(
        tmp_path / ".cursor" / "mcp.json",
        {"mcpServers": {"real": {"url": "http://real.example/mcp"}}},
    )

    manifests = collect_cursor_mcp_manifests([tmp_path], refs=_declared_refs(tmp_path))

    paths = {path for path, _ in manifests}
    assert plugin_dir / ".cursor" / "mcp.json" not in paths
    assert tmp_path / ".cursor" / "mcp.json" in paths


# --- Installed MCP collector: derived from refs, never a directory walk ---


def _mcp_ref(name: str, source_manifest: str, url: str | None = None) -> ComponentRef:
    extra = {
        "component_type": "mcp_server",
        "component_path": [{"type": "mcp_server", "name": name}],
    }
    if url is not None:
        extra["url"] = url
    return ComponentRef(
        name=name,
        source_manifest=source_manifest,
        source_locator=f"$.mcpServers.{name}",
        extra=extra,
    )


def test_installed_derives_manifest_from_refs_not_a_walk(tmp_path):
    mcp_path = tmp_path / "mcp.json"
    refs = [_mcp_ref("insecure", str(mcp_path), url="http://example.com/mcp")]

    manifests = collect_cursor_endpoint_mcp_manifests(tmp_path, None, refs)

    assert len(manifests) == 1
    path, data = manifests[0]
    assert path == mcp_path
    assert data["mcpServers"]["insecure"]["url"] == "http://example.com/mcp"


def test_installed_ignores_fixture_not_in_refs(tmp_path):
    """A directory walk would attribute a fixture the bundle never read; the
    installed collector must not see files at all, only refs."""
    _write(tmp_path / "mcp.json", {"mcpServers": {"phantom": {"url": "http://phantom/mcp"}}})

    assert collect_cursor_endpoint_mcp_manifests(tmp_path, None, []) == []


def test_installed_non_mcp_refs_ignored(tmp_path):
    ref = ComponentRef(
        name="skill",
        source_manifest=str(tmp_path / "SKILL.md"),
        extra={"component_type": "skill"},
    )

    assert collect_cursor_endpoint_mcp_manifests(tmp_path, None, [ref]) == []


# --- JSONC parsing ----------------------------------------------------------


def test_permissions_json_with_comments_and_trailing_comma_parses(tmp_path):
    path = _write_text(
        tmp_path / "permissions.json",
        """
        {
          // allow this one
          "mcpAllowlist": ["alpha", "beta",],
          "autoRun": ["gamma"], /* block comment */
        }
        """,
    )

    manifests = resolve_cursor_permissions([path])

    assert len(manifests) == 1
    _, data = manifests[0]
    permissions = data["cursor_permissions"]
    assert set(permissions["mcpAllowlist"]) == {"alpha", "beta"}
    assert permissions["autoRun"] == ["gamma"]


def test_malformed_permissions_json_is_skipped_not_raised(tmp_path):
    path = _write_text(tmp_path / "permissions.json", "{not json at all")

    assert resolve_cursor_permissions([path]) == []


# --- The merge concatenates, in both directions ----------------------------


def test_user_only_and_project_only_entries_both_survive(tmp_path):
    user_path = _write(tmp_path / "user" / "permissions.json", {"mcpAllowlist": ["user-only"]})
    project_path = _write(
        tmp_path / "project" / "permissions.json", {"mcpAllowlist": ["project-only"]}
    )

    manifests = resolve_cursor_permissions([user_path, project_path])

    assert len(manifests) == 1
    permissions = manifests[0][1]["cursor_permissions"]
    assert set(permissions["mcpAllowlist"]) == {"user-only", "project-only"}


def test_shared_field_from_both_files_concatenates_not_replaces(tmp_path):
    """Neither file's entries are dropped when both declare the field —
    the failure direction the brief calls the worse bug (missing an
    auto-approved server), so this pins concatenation, not either scope
    winning outright."""
    user_path = _write(tmp_path / "user" / "permissions.json", {"autoRun": ["shared", "user-x"]})
    project_path = _write(
        tmp_path / "project" / "permissions.json", {"autoRun": ["shared", "project-x"]}
    )

    manifests = resolve_cursor_permissions([user_path, project_path])

    permissions = manifests[0][1]["cursor_permissions"]
    assert permissions["autoRun"].count("shared") == 2
    assert "user-x" in permissions["autoRun"]
    assert "project-x" in permissions["autoRun"]


def test_project_only_entry_is_attributed_to_project_file_not_primary(tmp_path):
    """Regression for a Codex review finding: the primary tuple path (first
    existing file — here the user file) must not be reported as the
    `declared_by` source for an entry that only exists in the project file.
    That misdirects remediation at the wrong config file."""
    user_path = _write(tmp_path / "user" / "permissions.json", {"mcpAllowlist": ["user-only"]})
    project_path = _write(
        tmp_path / "project" / "permissions.json", {"mcpAllowlist": ["project-only"]}
    )

    manifests = resolve_cursor_permissions([user_path, project_path])

    findings = {f.component_label: f for f in check_mcp_auto_approve(manifests)}
    user_finding = findings["mcp-server/user-only autoApprove"]
    project_finding = findings["mcp-server/project-only autoApprove"]
    assert user_finding.declared_by is not None
    assert project_finding.declared_by is not None
    assert user_finding.declared_by["path"] == str(user_path)
    assert project_finding.declared_by["path"] == str(project_path)


def test_shared_entry_attributed_to_more_specific_project_file(tmp_path):
    """When both scopes declare the same server, the single resulting finding
    is attributed to the project file — the more specific, most-actionable
    location — not the user file just because it happens to be primary."""
    user_path = _write(tmp_path / "user" / "permissions.json", {"autoRun": ["shared"]})
    project_path = _write(tmp_path / "project" / "permissions.json", {"autoRun": ["shared"]})

    manifests = resolve_cursor_permissions([user_path, project_path])

    findings = check_mcp_auto_approve(manifests)
    assert len(findings) == 1
    declared_by = findings[0].declared_by
    assert declared_by is not None
    assert declared_by["path"] == str(project_path)


def test_missing_project_file_keeps_user_entries(tmp_path):
    user_path = _write(tmp_path / "user" / "permissions.json", {"mcpAllowlist": ["only-user"]})
    missing_project_path = tmp_path / "project" / "permissions.json"

    manifests = resolve_cursor_permissions([user_path, missing_project_path])

    permissions = manifests[0][1]["cursor_permissions"]
    assert permissions["mcpAllowlist"] == ["only-user"]


# --- Env-var relocation for the installed user-scope file ------------------


def test_cursor_config_dir_wins_over_xdg(tmp_path, monkeypatch):
    override_dir = tmp_path / "override"
    _write(override_dir / "permissions.json", {"mcpAllowlist": ["from-override"]})
    xdg_dir = tmp_path / "xdg"
    _write(xdg_dir / "cursor" / "permissions.json", {"mcpAllowlist": ["from-xdg"]})
    monkeypatch.setenv("CURSOR_CONFIG_DIR", str(override_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))

    manifests = collect_cursor_endpoint_permissions_manifests(tmp_path, None)

    permissions = manifests[0][1]["cursor_permissions"]
    assert permissions["mcpAllowlist"] == ["from-override"]


def test_xdg_config_home_resolves_to_cursor_subdir_when_no_override(tmp_path, monkeypatch):
    xdg_dir = tmp_path / "xdg"
    _write(xdg_dir / "cursor" / "permissions.json", {"mcpAllowlist": ["from-xdg"]})
    monkeypatch.delenv("CURSOR_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))

    manifests = collect_cursor_endpoint_permissions_manifests(tmp_path, None)

    permissions = manifests[0][1]["cursor_permissions"]
    assert permissions["mcpAllowlist"] == ["from-xdg"]


def test_falls_back_to_home_dot_cursor_when_neither_var_set(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    _write(fake_home / ".cursor" / "permissions.json", {"mcpAllowlist": ["from-home"]})
    monkeypatch.delenv("CURSOR_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("tools.posture.Path.home", staticmethod(lambda: fake_home))

    manifests = collect_cursor_endpoint_permissions_manifests(tmp_path, None)

    permissions = manifests[0][1]["cursor_permissions"]
    assert permissions["mcpAllowlist"] == ["from-home"]


def test_installed_permissions_ignores_config_dir_param(tmp_path, monkeypatch):
    """`permissions.json` relocates independently of the general Cursor
    config root — a `config_dir` that differs from the env-resolved
    location must not be consulted for it."""
    fake_home = tmp_path / "home"
    _write(fake_home / ".cursor" / "permissions.json", {"mcpAllowlist": ["from-home"]})
    unrelated_config_dir = tmp_path / "unrelated" / ".cursor"
    _write(unrelated_config_dir / "permissions.json", {"mcpAllowlist": ["should-not-be-read"]})
    monkeypatch.delenv("CURSOR_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("tools.posture.Path.home", staticmethod(lambda: fake_home))

    manifests = collect_cursor_endpoint_permissions_manifests(unrelated_config_dir, None)

    permissions = manifests[0][1]["cursor_permissions"]
    assert permissions["mcpAllowlist"] == ["from-home"]


# --- Declared permissions collector: repo-relative only --------------------


def test_declared_permissions_reads_project_file(tmp_path):
    _write(tmp_path / ".cursor" / "permissions.json", {"mcpAllowlist": ["project-server"]})

    manifests = collect_cursor_permissions_manifests([tmp_path])

    permissions = manifests[0][1]["cursor_permissions"]
    assert permissions["mcpAllowlist"] == ["project-server"]


def test_declared_permissions_never_reads_home_directory(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    _write(fake_home / ".cursor" / "permissions.json", {"mcpAllowlist": ["should-not-appear"]})
    monkeypatch.setattr("tools.posture.Path.home", staticmethod(lambda: fake_home))
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    assert collect_cursor_permissions_manifests([repo_root]) == []


# --- mcp_auto_approve branches on manifest shape ----------------------------


def test_mcp_auto_approve_branches_on_cursor_permissions_shape(tmp_path):
    path = tmp_path / "permissions.json"
    manifest = {"cursor_permissions": {"mcpAllowlist": ["allowed-server"], "autoRun": []}}

    findings = check_mcp_auto_approve([(path, manifest)])

    assert len(findings) == 1
    assert findings[0].rule_id == "openaca-posture-mcp-auto-approve"
    assert "allowed-server" in findings[0].component_label
    assert findings[0].active_in == ["cursor"]


def test_mcp_auto_approve_cursor_dedupes_name_in_both_fields(tmp_path):
    path = tmp_path / "permissions.json"
    manifest = {"cursor_permissions": {"mcpAllowlist": ["srv"], "autoRun": ["srv"]}}

    findings = check_mcp_auto_approve([(path, manifest)])

    assert len(findings) == 1


def test_mcp_auto_approve_claude_code_shape_still_unedited(tmp_path):
    """The existing Claude Code branch (no `cursor_permissions` key) must
    keep working exactly as before this task."""
    manifest = {"mcpServers": {"unsafe": {"url": "https://x.example/mcp", "autoApprove": True}}}

    findings = check_mcp_auto_approve([(tmp_path / ".mcp.json", manifest)])

    assert len(findings) == 1
    assert findings[0].active_in == ["claude-code"]


# --- The scan.py / collector.py boundaries still call through cleanly -----


def test_scan_agent_scan_prep_and_remote_collector_reference_shared_helper():
    """Both consolidated call sites are reachable/importable post-refactor —
    a regression here would be an ImportError, not a silent behavior change."""
    assert callable(_scan_agent_scan_prep)
    assert callable(_remote_agent_posture_manifests)
