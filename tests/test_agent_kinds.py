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

    agents = discover_agents(DiscoveryContext(source="installed", config_dir=tmp_path))

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

    agents = discover_agents(DiscoveryContext(source="installed", config_dir=empty_root))

    assert len(agents) == 1


def test_installed_discovery_yields_nothing_when_the_root_is_absent(tmp_path):
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
