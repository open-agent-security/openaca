import json
from pathlib import Path

import pytest

import tools.agent_kinds as agent_kinds
from tools.agent_kinds import (
    AgentInstance,
    AgentKind,
    DiscoveryContext,
    discover_agents,
    kind_for,
    output_basenames,
    resolve_coverage,
    slugify_agent_id,
)
from tools.agent_kinds.claude_code import declared_evidence
from tools.graph import Graph


def test_claude_code_installed_discovery_yields_one_agent(tmp_path):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")

    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=tmp_path, kind_id="claude-code")
    )

    assert len(agents) == 1
    agent = agents[0]
    assert agent.kind_id == "claude-code"
    assert agent.display_name == "Claude Code"
    assert agent.agent_id is None
    assert agent.bom_ref == "root/claude-code"
    assert agent.root_label == "claude-code"
    assert agent.coverage_baseline == "complete"
    assert agent.config_root == tmp_path


def test_installed_agent_with_no_configuration_is_still_an_agent(tmp_path):
    empty_root = tmp_path / ".claude"
    empty_root.mkdir()

    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=empty_root, kind_id="claude-code")
    )

    assert len(agents) == 1


def test_kind_id_filters_discovery_to_one_kind(tmp_path):
    """An explicit `kind_id` limits discovery to that kind only — a bare
    `config_dir` that would otherwise match every installed kind's "the root
    exists" rule only ever produces the selected kind's agent."""
    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=tmp_path, kind_id="claude-code")
    )

    assert [a.kind_id for a in agents] == ["claude-code"]


def test_kind_id_none_discovers_every_installed_kind_at_its_own_root(tmp_path, monkeypatch):
    """No `kind_id` means every registered kind resolves its own default root
    — with both `~/.claude` and `~/.cursor` present, both are discovered from
    one call with no explicit `config_dir` at all."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".claude").mkdir()
    (fake_home / ".cursor").mkdir()
    monkeypatch.setattr("tools.agent_kinds.claude_code.Path.home", lambda: fake_home)
    monkeypatch.setattr("tools.agent_kinds.cursor.Path.home", lambda: fake_home)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)

    agents = discover_agents(DiscoveryContext(source="installed"))

    assert {a.kind_id for a in agents} == {"claude-code", "cursor"}


def test_installed_discovery_yields_nothing_when_the_root_is_absent(tmp_path, monkeypatch):
    """Home is faked rather than `config_dir` passed: Cursor declares no
    relocatable root (ADR-0054), so a `config_dir` pointing at nothing would
    still discover the invoking user's real `~/.cursor`."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "empty-home"))

    agents = discover_agents(DiscoveryContext(source="installed", config_dir=tmp_path / "missing"))

    assert agents == []


def test_singleton_kind_must_not_carry_an_agent_id():
    with pytest.raises(ValueError, match="singleton"):
        AgentInstance(
            kind_id="claude-code",
            display_name="Claude Code",
            source="installed",
            root_label="claude-code",
            coverage_baseline="complete",
            agent_id="oops",
        ).validate_against(kind_for("claude-code"))


def test_resolve_coverage_never_raises_the_baseline():
    assert resolve_coverage("complete", evidence_gaps=0) == "complete"
    assert resolve_coverage("complete", evidence_gaps=1) == "partial"
    assert resolve_coverage("partial", evidence_gaps=0) == "partial"
    assert resolve_coverage("unknown", evidence_gaps=3) == "unknown"


def test_slugify_agent_id_is_filesystem_safe_and_stable():
    assert slugify_agent_id("researcher") == "researcher"
    assert slugify_agent_id("Payments/Triage") == "payments-triage"
    assert slugify_agent_id("  spaced  name ") == "spaced-name"
    long = slugify_agent_id("x" * 200)
    assert len(long) <= 64
    assert long == slugify_agent_id("x" * 200)


def test_output_basenames_disambiguate_slug_collisions():
    def instance(agent_id):
        return AgentInstance(
            kind_id="synthetic",
            display_name=agent_id,
            source="installed",
            root_label="synthetic",
            coverage_baseline="partial",
            agent_id=agent_id,
        )

    a, b = instance("Payments/Triage"), instance("payments-triage")
    names = output_basenames([a, b])

    assert names[a.bom_ref] != names[b.bom_ref]
    assert all(name.startswith("synthetic--payments-triage") for name in names.values())


def test_discover_agents_rejects_a_duplicate_instance_key(monkeypatch):
    def discover_two_with_the_same_id(ctx):
        return [
            AgentInstance(
                kind_id="synthetic",
                display_name="Synthetic",
                source="installed",
                root_label="synthetic",
                coverage_baseline="partial",
                agent_id="dup",
            )
            for _ in range(2)
        ]

    broken_kind = AgentKind(
        id="synthetic",
        display_name="Synthetic",
        cardinality="many_per_place",
        root_label="synthetic",
        coverage_baseline={"installed": "partial", "declared": "partial"},
        discover=discover_two_with_the_same_id,
        compose=lambda agent, **_: Graph(nodes={}),
    )
    monkeypatch.setattr(agent_kinds, "REGISTRY", (broken_kind,))

    with pytest.raises(ValueError, match="duplicate agent instance key"):
        discover_agents(DiscoveryContext(source="installed"))


def test_claude_code_declares_the_full_manifest_registry_as_its_surface():
    """Guards against the kind's declared surface silently narrowing (or
    widening) apart from `tools.parsers.REGISTRY` — the two are meant to be
    the same list by construction, not independently maintained."""
    from tools.agent_kinds import claude_code
    from tools.parsers import REGISTRY

    assert claude_code.KIND.manifest_patterns == tuple(REGISTRY)
    assert claude_code.KIND.posture_manifest_collectors is not None
    assert claude_code.KIND.installed_posture_collectors is not None


def test_a_kind_cannot_allowlist_an_unknown_posture_rule():
    with pytest.raises(ValueError, match="unknown posture rule"):
        AgentKind(
            id="synthetic",
            display_name="Synthetic",
            cardinality="singleton",
            root_label="synthetic",
            coverage_baseline={"installed": "complete", "declared": "complete"},
            discover=lambda ctx: [],
            compose=lambda agent, **_: Graph(nodes={}),
            posture_rules=frozenset({"not-a-real-rule-id"}),
        )


def test_a_kind_may_allowlist_a_known_posture_rule():
    from tools.posture import KNOWN_RULE_IDS

    kind = AgentKind(
        id="synthetic",
        display_name="Synthetic",
        cardinality="singleton",
        root_label="synthetic",
        coverage_baseline={"installed": "complete", "declared": "complete"},
        discover=lambda ctx: [],
        compose=lambda agent, **_: Graph(nodes={}),
        posture_rules=frozenset({next(iter(sorted(KNOWN_RULE_IDS)))}),
    )

    assert kind.posture_rules is not None


def test_declared_discovery_finds_an_agent_from_an_owned_file(tmp_path):
    skill = tmp_path / "apps" / "web" / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert len(agents) == 1
    assert agents[0].source == "declared"
    assert agents[0].bom_ref == "root/claude-code"
    assert agents[0].scan_root == tmp_path


def test_nested_dot_directories_are_one_agent(tmp_path):
    for app in ("web", "api"):
        d = tmp_path / "apps" / app / ".claude"
        d.mkdir(parents=True)
        (d / "settings.json").write_text("{}", encoding="utf-8")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert len(agents) == 1


def test_a_repo_of_ordinary_manifests_declares_no_agent(tmp_path):
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    (tmp_path / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    assert declared_evidence(tmp_path) is None
    assert discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path)) == []


def test_an_empty_claude_directory_is_not_evidence(tmp_path):
    (tmp_path / ".claude").mkdir()

    assert declared_evidence(tmp_path) is None


def test_project_mcp_json_is_evidence(tmp_path):
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    assert declared_evidence(tmp_path) is not None


def test_an_unrecognized_file_under_claude_is_not_evidence(tmp_path):
    """`.claude/` holding *some* file is not by itself proof of a composition
    surface — only the enumerated, recognized ones are."""
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "CLAUDE.md").write_text("# notes", encoding="utf-8")

    assert declared_evidence(tmp_path) is None


# --- Cursor kind -------------------------------------------------------
#
# `cursor.KIND` is registered in `agent_kinds.REGISTRY` (Task 9's last step).
# Most tests below still exercise `cursor.discover`/`cursor.declared_evidence`
# directly rather than through `discover_agents`, since they target Cursor's
# own evidence/discovery rules in isolation.


def test_cursor_kind_is_registered():
    from tools.agent_kinds import cursor

    assert cursor.KIND.id in {kind.id for kind in agent_kinds.REGISTRY}


def test_installed_shared_skill_composes_once_per_agent_not_deduplicated_or_doubled(
    tmp_path, monkeypatch
):
    """Task 9 Step 6, pinned on the real installed path (not just the
    synthetic `scan repo` fixture): Cursor's `.claude/*` compat read
    genuinely composes from Claude Code's own skills root, so a skill file
    under `~/.claude/skills/` is reachable by both registered kinds today.
    Each kind's own graph gets its own single occurrence of the shared
    file — not deduplicated away across agents, and not doubled within
    either agent's own graph."""
    fake_home = tmp_path / "home"
    skill_dir = fake_home / ".claude" / "skills" / "shared-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: Shared between both kinds\n---\n"
        "Run the shared workflow.\n",
        encoding="utf-8",
    )
    (fake_home / ".cursor").mkdir(parents=True)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr("tools.agent_kinds.claude_code.Path.home", lambda: fake_home)
    monkeypatch.setattr("tools.agent_kinds.cursor.Path.home", lambda: fake_home)
    monkeypatch.setattr("tools.graph_build_cursor.Path.home", lambda: fake_home)

    agents = discover_agents(DiscoveryContext(source="installed"))

    assert {a.kind_id for a in agents} == {"claude-code", "cursor"}
    for agent in agents:
        graph = agent_kinds.build_agent_graph(agent)
        skill_nodes = [n for n in graph.nodes.values() if n.kind == "skill"]
        assert len(skill_nodes) == 1, (agent.kind_id, skill_nodes)


def test_cursor_a_claude_only_tree_declares_no_cursor_agent(tmp_path):
    from tools.agent_kinds import cursor

    skill = tmp_path / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    assert cursor.declared_evidence(tmp_path) is None
    assert cursor.discover(DiscoveryContext(source="declared", scan_root=tmp_path)) == []


def test_cursor_mcp_json_is_evidence_of_a_declared_cursor_agent(tmp_path):
    from tools.agent_kinds import cursor

    mcp = tmp_path / ".cursor" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text('{"mcpServers": {}}', encoding="utf-8")

    assert cursor.declared_evidence(tmp_path) is not None
    agents = cursor.discover(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert len(agents) == 1
    agent = agents[0]
    assert agent.kind_id == "cursor"
    assert agent.source == "declared"
    assert agent.scan_root == tmp_path
    assert agent.coverage_baseline == "partial"
    assert agent.bom_ref == "root/cursor"


def test_cursor_bare_mcp_json_is_not_evidence(tmp_path):
    from tools.agent_kinds import cursor

    (tmp_path / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")

    assert cursor.declared_evidence(tmp_path) is None


def test_cursor_agents_skills_is_evidence(tmp_path):
    from tools.agent_kinds import cursor

    skill = tmp_path / ".agents" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\n", encoding="utf-8")

    assert cursor.declared_evidence(tmp_path) is not None


def test_cursor_schema_detected_root_plugin_json_is_evidence(tmp_path):
    from tools.agent_kinds import cursor

    manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "demo",
    }
    (tmp_path / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert cursor.declared_evidence(tmp_path) is not None


def test_cursor_an_unrelated_plugin_json_is_not_evidence(tmp_path):
    from tools.agent_kinds import cursor

    (tmp_path / "plugin.json").write_text('{"name": "not-agent-plugins"}', encoding="utf-8")

    assert cursor.declared_evidence(tmp_path) is None


def test_cursor_installed_discovery_yields_one_agent(tmp_path, monkeypatch):
    from tools.agent_kinds import cursor

    home = tmp_path / "home"
    root = home / ".cursor"
    root.mkdir(parents=True)
    (root / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    agents = cursor.discover(DiscoveryContext(source="installed"))

    assert len(agents) == 1
    agent = agents[0]
    assert agent.kind_id == "cursor"
    assert agent.coverage_baseline == "partial"
    assert agent.config_root == root


def test_cursor_ignores_config_dir_and_always_resolves_home(tmp_path, monkeypatch):
    """ADR-0054: Cursor declares no relocatable root. A `config_dir` that
    reached discovery (the CLI rejects it first) must not move the root — the
    alternative is a composition stitched from two homes."""
    from tools.agent_kinds import cursor

    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    agents = cursor.discover(
        DiscoveryContext(source="installed", config_dir=tmp_path / "elsewhere")
    )

    assert [a.config_root for a in agents] == [home / ".cursor"]


def test_cursor_config_root_ignores_cursor_config_dir_env(tmp_path, monkeypatch):
    """`CURSOR_CONFIG_DIR` scopes only `permissions.json`/the CLI's own
    config, never the whole config root (docs/specs/cursor-agent-kind.md
    "Config root")."""
    from tools.agent_kinds import cursor

    relocated = tmp_path / "relocated"
    relocated.mkdir()
    monkeypatch.setenv("CURSOR_CONFIG_DIR", str(relocated))

    assert cursor.resolve_config_root(None) == Path.home() / ".cursor"


def test_cursor_kind_posture_allowlist_validates_at_import():
    from tools.agent_kinds import cursor
    from tools.posture import KNOWN_RULE_IDS
    from tools.posture.rules import api_endpoint_override

    assert cursor.KIND.posture_rules is not None
    assert cursor.KIND.posture_rules <= KNOWN_RULE_IDS
    assert api_endpoint_override.RULE_ID not in cursor.KIND.posture_rules


def test_cursor_kind_declares_host_agnostic_plus_cursor_manifest_surface():
    from tools.agent_kinds import cursor
    from tools.parsers import CURSOR_MANIFEST_REGISTRY, HOST_AGNOSTIC_REGISTRY

    assert cursor.KIND.manifest_patterns == tuple(HOST_AGNOSTIC_REGISTRY) + tuple(
        CURSOR_MANIFEST_REGISTRY
    )
    assert cursor.KIND.posture_manifest_collectors is not None
    assert cursor.KIND.installed_posture_collectors is not None


def test_cursor_kind_is_singleton_with_no_agent_id():
    from tools.agent_kinds import cursor

    with pytest.raises(ValueError, match="singleton"):
        AgentInstance(
            kind_id="cursor",
            display_name="Cursor",
            source="installed",
            root_label="cursor",
            coverage_baseline="partial",
            agent_id="oops",
        ).validate_against(cursor.KIND)
