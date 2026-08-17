from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.component_ref import ComponentRef
from tools.graph_build import build_graph
from tools.host_paths import owning_host
from tools.parsers import parse_repo, parse_repo_grouped, resolve_host_selection

REPOS = Path(__file__).parent.parent / "fixtures" / "repos"


def test_parse_repo_combines_all_manifests():
    refs = []
    for sample in ["sample-npm", "sample-mcp", "sample-plugin", "sample-settings"]:
        refs += parse_repo(REPOS / sample)

    purls = {r.purl for r in refs if r.purl}
    identities = {r.component_identity for r in refs if r.component_identity}

    assert "pkg:npm/%40cyanheads/git-mcp-server@1.1.0" in purls
    assert "pkg:pypi/weather-mcp@0.5.0" in purls
    assert "pkg:pypi/sketchy-mcp" in purls
    assert any(i.startswith("plugin/") for i in identities)


def test_one_malformed_manifest_does_not_abort_scan(tmp_path):
    """A repo with both a broken and a valid manifest should still emit refs."""
    (tmp_path / "package.json").write_text("not valid json")
    (tmp_path / "mcp.json").write_text(
        '{"mcpServers": {"git": {"command": "npx", "args": ["@scope/server@1.2.3"]}}}'
    )
    refs = parse_repo(tmp_path)
    purls = {r.purl for r in refs if r.purl}
    assert "pkg:npm/%40scope/server@1.2.3" in purls


def test_dep_manifest_without_plugin_marker_classified_as_software(tmp_path):
    """A bare package.json (no .claude-plugin/plugin.json sibling) → its deps
    are software-dependency. Scope now comes from the composition graph
    (`Graph.scope_of`), not a path heuristic in the parser — `parse_repo`
    itself no longer classifies scope."""
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}'
    )
    graph = build_graph(tmp_path, mode="repo")
    scopes = {graph.scope_of(n) for n in graph.nodes.values() if n.ref and n.ref.ecosystem == "npm"}
    assert scopes == {"software-dependency"}


def test_dep_manifest_co_located_with_plugin_classified_as_agent_dep(tmp_path):
    """The same package.json but with a sibling .claude-plugin/plugin.json
    becomes agent-dependency — its deps hang off the plugin node in the graph,
    so `scope_of` sees a plugin ancestor."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"my-plugin","version":"1.0.0"}'
    )
    (tmp_path / "package.json").write_text(
        '{"name":"my-plugin","version":"1.0.0","dependencies":{"lodash":"4.17.20"}}'
    )
    graph = build_graph(tmp_path, mode="repo")
    npm_scopes = {
        graph.scope_of(n) for n in graph.nodes.values() if n.ref and n.ref.ecosystem == "npm"
    }
    assert npm_scopes == {"agent-dependency"}
    # The plugin self-identity node stays agent-component.
    cp_scopes = {
        graph.scope_of(n)
        for n in graph.nodes.values()
        if n.ref and (n.ref.extra or {}).get("component_type") == "plugin"
    }
    assert cp_scopes == {"agent-component"}


def test_cursor_mcp_json_repo_scan(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["cursor"])
    assert n_found == 1
    refs = grouped[0][1]
    assert refs[0].extra["runtime_hosts"] == ["cursor"]


def test_cursor_mcp_json_excluded_when_host_not_selected(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code"])
    assert n_found == 0
    assert grouped == []


def test_cursor_commands_registered_and_discovered(tmp_path):
    cursor_commands = tmp_path / ".cursor" / "commands"
    cursor_commands.mkdir(parents=True)
    (cursor_commands / "deploy.md").write_text("run\n")
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][1][0].extra["runtime_hosts"] == ["cursor"]
    assert grouped[0][1][0].extra["component_type"] == "command"


def test_default_hosts_is_every_registered_host(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path)  # hosts omitted
    assert n_found == 1  # Cursor's manifest is found without being asked for explicitly


def test_lockfile_manifests_are_host_agnostic(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x", "dependencies": {"lodash": "4.17.20"}}')
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["cursor"])
    # package.json has no host concept — found regardless of which hosts are selected.
    assert n_found == 1


def test_cursor_mcp_json_not_double_counted_when_both_hosts_selected(tmp_path):
    # Regression guard: .cursor/mcp.json shares a basename with Claude's
    # bare mcp.json pattern. Without the owning_host exclusion (Step 3),
    # this file matches both registry entries and n_found becomes 2 for
    # one file, with two conflicting refs (one mistagged claude-code).
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert len(grouped) == 1
    refs = grouped[0][1]
    assert refs[0].extra["runtime_hosts"] == ["cursor"]


def test_claude_bare_mcp_json_still_matches_when_cursor_also_selected(tmp_path):
    # The exclusion must be narrow: a plain (non-.cursor) mcp.json must
    # still match Claude's pattern even when Cursor is also selected.
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][1][0].extra["runtime_hosts"] == ["claude-code"]


def test_synthetic_host_registered_in_hosts_is_discoverable_without_touching_registry(
    tmp_path, monkeypatch
):
    # Proves that registering a HostAdapter is *sufficient* for repo-mode
    # accounting to pick it up, with no edit to this module. This test
    # can pass now, for exactly this half — registering the adapter
    # alone is enough here, because _active_registry reads
    # HOSTS[host_id].manifest_registry directly. It does NOT prove the
    # same for the graph (tools/graph_build.py's descend()), which still
    # needs a hand-written branch per host regardless of what's
    # registered in HOSTS — that half stays the open question.
    from tools.hosts import HOSTS, HostAdapter

    def _synthetic_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="synthetic-widget",
                extra={"component_type": "mcp_server", "runtime_hosts": ["synthetic-host"]},
            )
        ]

    synthetic_adapter = HostAdapter(
        host_id="synthetic-host",
        detect=lambda: False,
        config_root=lambda override: None,
        manifest_registry=[("synthetic.json", _synthetic_parse)],
        posture_rule_ids=frozenset(),
    )
    monkeypatch.setitem(HOSTS, "synthetic-host", synthetic_adapter)

    (tmp_path / "synthetic.json").write_text("{}")
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["synthetic-host"])
    assert n_found == 1
    assert grouped[0][1][0].name == "synthetic-widget"


def test_cursor_cache_mcp_json_is_claude_owned_not_invisible(tmp_path):
    # Boundary case: .cursor/cache/mcp.json is nested
    # UNDER .cursor/ but is not the exact .cursor/mcp.json shape Cursor's
    # pattern matches. It must fall back to Claude's catch-all (found,
    # tagged claude-code) — not silently invisible to both patterns,
    # which is what a loose "under .cursor/" classifier would produce.
    nested = tmp_path / ".cursor" / "cache"
    nested.mkdir(parents=True)
    (nested / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}})
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][1][0].extra["runtime_hosts"] == ["claude-code"]


def test_owning_host_cursor_root_mcp_json():
    assert owning_host(Path("repo/.cursor/mcp.json")) == "cursor"


def test_owning_host_cursor_nested_mcp_json():
    # Nested project (packages/frontend/.cursor/mcp.json) — same
    # depth-independent shape _is_cursor_mcp_json already recognizes.
    assert owning_host(Path("repo/packages/frontend/.cursor/mcp.json")) == "cursor"


def test_owning_host_claude_bare_mcp_json():
    assert owning_host(Path("repo/.mcp.json")) == "claude-code"
    assert owning_host(Path("repo/plugins/foo/mcp.json")) == "claude-code"


def test_owning_host_cursor_dotfile_variant_not_cursor():
    # Boundary case: .cursor/.mcp.json is NOT the
    # real Cursor convention (filename has the leading dot; Cursor's
    # own docs and this module's registry/graph dispatch only recognize
    # bare "mcp.json"). A loose ".cursor is an ancestor" check would
    # wrongly call this "cursor" even though neither the graph's
    # Cursor branch nor the registry's Cursor pattern would ever
    # match this exact filename — falls back to claude-code, the same
    # answer it would get if Cursor didn't exist at all.
    assert owning_host(Path("repo/.cursor/.mcp.json")) == "claude-code"


def test_owning_host_cursor_subdirectory_not_cursor():
    # .cursor/cache/mcp.json: nested under .cursor/ but not directly
    # in it — not the real convention either. Same reasoning as above.
    assert owning_host(Path("repo/.cursor/cache/mcp.json")) == "claude-code"


def test_parse_repo_grouped_rejects_colliding_host_patterns(tmp_path, monkeypatch):
    from tools.hosts import HOSTS, HostAdapter

    def _collider_parse(path: Path) -> list[ComponentRef]:
        return [
            ComponentRef(
                name="collider",
                extra={"component_type": "mcp_server", "runtime_hosts": ["collider-host"]},
            )
        ]

    collider_adapter = HostAdapter(
        host_id="collider-host",
        detect=lambda: False,
        config_root=lambda override: None,
        # Same bare pattern Claude's adapter already owns — this is
        # exactly the ambiguous reuse resolve_host_selection
        # exists to reject, not a new pattern shape.
        manifest_registry=[("mcp.json", _collider_parse)],
        posture_rule_ids=frozenset(),
    )
    monkeypatch.setitem(HOSTS, "collider-host", collider_adapter)

    (tmp_path / "mcp.json").write_text("{}")
    with pytest.raises(
        ValueError,
        match="mcp.json.*claude-code.*collider-host|mcp.json.*collider-host.*claude-code",
    ):
        parse_repo_grouped(tmp_path, hosts=["claude-code", "collider-host"])


def test_parse_repo_grouped_dedupes_duplicate_host_ids(tmp_path):
    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        '{"mcpServers": {"weather": {"command": "npx", "args": ["weather-mcp@1.0.0"]}}}'
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["cursor", "cursor"])
    assert n_found == 1
    assert len(grouped) == 1


def test_resolve_host_selection_rejects_unknown_host():
    with pytest.raises(ValueError, match="typo"):
        resolve_host_selection(["claude-code", "typo"])


def test_parse_repo_grouped_rejects_unknown_host(tmp_path):
    with pytest.raises(ValueError, match="typo"):
        parse_repo_grouped(tmp_path, hosts=["typo"])


def test_parse_repo_grouped_subagent_precedence_no_override(tmp_path):
    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "helper.md").write_text("---\nname: helper\n---\nh\n")
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][1][0].extra["runtime_hosts"] == ["claude-code", "cursor"]


def test_parse_repo_grouped_malformed_subagent_does_not_abort_others(tmp_path, monkeypatch):
    # One malformed subagent .md must cost only that one file — the sibling
    # subagent must still show up in the manifest accounting.
    from tools.parsers import claude_command_agent

    claude_agents = tmp_path / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (claude_agents / "broken.md").write_text("---\nname: broken\n---\nx\n")
    (claude_agents / "healthy.md").write_text("---\nname: healthy\n---\ny\n")

    real_parse_file = claude_command_agent.parse_file

    def flaky_parse_file(path, *args, **kwargs):
        if path.name == "broken.md":
            raise ValueError("simulated parse failure")
        return real_parse_file(path, *args, **kwargs)

    monkeypatch.setattr(claude_command_agent, "parse_file", flaky_parse_file)

    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    assert grouped[0][0].name == "healthy.md"


def test_cursor_plugin_json_registered_and_discovered(tmp_path):
    plugin_dir = tmp_path / ".cursor-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"name": "demo"}')
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1
    plugin_refs = [
        r
        for refs in [g[1] for g in grouped]
        for r in refs
        if r.extra.get("component_type") == "plugin"
    ]
    assert plugin_refs[0].extra["runtime_hosts"] == ["cursor"]
    assert plugin_refs[0].component_identity == "plugin/demo"


# Agent Plugins (open standard): schema-detected bespoke dispatch outside
# manifest_registry. See docs/specs/multi-host-support.md Plugins section
# and ADR-0045 Decision #3.


def test_agent_plugins_root_plugin_json_detected_by_schema(tmp_path):
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "demo"}'
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 1


def test_unrelated_root_plugin_json_not_matched_as_agent_plugins(tmp_path):
    (tmp_path / "plugin.json").write_text('{"name": "unrelated-config"}')
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 0


def test_agent_plugins_not_detected_when_cursor_not_selected(tmp_path):
    # The bespoke content-based dispatch honors the same selected-host gate
    # as registry entries: a Claude-only scan must not emit a Cursor-tagged
    # Agent Plugin.
    (tmp_path / "plugin.json").write_text(
        '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "demo"}'
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code"])
    assert n_found == 0


def test_both_plugin_formats_in_same_directory_both_parsed(tmp_path):
    cursor_plugin_dir = tmp_path / ".cursor-plugin"
    cursor_plugin_dir.mkdir()
    (cursor_plugin_dir / "plugin.json").write_text('{"name": "native-demo"}')
    (tmp_path / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "open-demo",
            }
        )
    )
    grouped, n_found = parse_repo_grouped(tmp_path, hosts=["claude-code", "cursor"])
    assert n_found == 2
