"""The Devin CLI kind (ADR-0070, ADR-0071, ADR-0072)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tools.agent_kinds import DiscoveryContext, devin_cli


def _write(path: Path, content: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _skill(path: Path) -> Path:
    return _write(path / "SKILL.md", f"---\nname: {path.name}\n---\nS\n")


def _declared(scan_root: Path):
    return devin_cli.discover(DiscoveryContext(source="declared", scan_root=scan_root))


# --- Declared evidence ---------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        ".devin/mcp_config.json",
        ".devin/config.json",
        ".devin/hooks.v1.json",
        ".devin/agents/reviewer.md",
        ".devin/agents/researcher/AGENT.md",
        ".devin-plugin/plugin.json",
        ".agents/agents/reviewer.md",
        "packages/app/.devin/mcp_config.json",
    ],
)
def test_devin_owned_files_are_evidence(tmp_path, rel):
    content = json.dumps({"name": "p"}) if rel.endswith("plugin.json") else "{}"
    _write(tmp_path / rel, content)

    assert devin_cli.declared_evidence(tmp_path) is not None
    assert len(_declared(tmp_path)) == 1


@pytest.mark.parametrize("root", [".devin/skills", ".cognition/skills", ".agents/skills"])
def test_skill_roots_devin_reads_natively_are_evidence(tmp_path, root):
    _skill(tmp_path / root / "s")

    assert devin_cli.declared_evidence(tmp_path) is not None


@pytest.mark.parametrize(
    "rel",
    [
        ".devin/mcp_config.local.json",
        ".devin/config.local.json",
        ".mcp.json",
        ".claude/settings.json",
        ".claude/commands/deploy.md",
        ".cursor/mcp.json",
        ".claude-plugin/plugin.json",
        "AGENTS.md",
        ".devin/rules/style.md",
        ".devin/agents/notes.txt",
        ".agents/agents/example/config.json",
        ".devin/agents/sub/notes.md",
        ".devin/skills/sub/extra/SKILL.md",
    ],
)
def test_composition_only_files_are_not_evidence(tmp_path, rel):
    """Gitignored local layers are incidental, imported files belong to
    another runtime, instruction files are not configuration, a file under
    `agents/` that composition does not load (wrong extension, or a
    directory-form profile under the wrong filename) is not evidence, and
    neither is one nested a level deeper than composition ever reads (a flat
    `.md` two directories under `agents/`, or a `SKILL.md` two directories
    under `skills/`) -- `*` in these patterns matches within one path
    segment, never across a `/`."""
    content = json.dumps({"name": "p"}) if rel.endswith("plugin.json") else "{}"
    _write(tmp_path / rel, content)

    assert devin_cli.declared_evidence(tmp_path) is None
    assert _declared(tmp_path) == []


@pytest.mark.parametrize("root", [".claude/skills", ".windsurf/skills", ".github/skills"])
def test_imported_skill_roots_are_not_evidence(tmp_path, root):
    _skill(tmp_path / root / "s")

    assert devin_cli.declared_evidence(tmp_path) is None


def test_a_devin_surface_inside_a_realized_plugin_is_not_evidence(tmp_path):
    """A plugin's own fixture content must not trip a phantom Devin BOM."""
    _write(tmp_path / ".claude-plugin" / "plugin.json", json.dumps({"name": "outer"}))
    _write(tmp_path / "examples" / ".devin" / "mcp_config.json", "{}")

    assert devin_cli.declared_evidence(tmp_path) is None


def test_the_devin_plugin_manifest_is_evidence_even_as_its_own_root(tmp_path):
    _write(tmp_path / ".devin-plugin" / "plugin.json", json.dumps({"name": "outer"}))
    _write(tmp_path / "examples" / ".devin" / "mcp_config.json", "{}")

    evidence = devin_cli.declared_evidence(tmp_path)

    assert evidence is not None and "examples" not in str(evidence)


def test_a_shared_agents_agents_repo_declares_only_devin(tmp_path):
    """ADR-0072: `.agents/agents/` is read by Devin CLI alone today."""
    from tools.agent_kinds import DiscoveryContext, discover_agents

    _write(tmp_path / ".agents" / "agents" / "reviewer.md", "Review.\n")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert [a.kind_id for a in agents] == ["devin-cli"]


def test_a_shared_skills_repo_declares_every_kind_that_reads_it(tmp_path):
    """ADR-0058's stated cost, applied to a third reader."""
    from tools.agent_kinds import DiscoveryContext, discover_agents

    _skill(tmp_path / ".agents" / "skills" / "shared")

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert {a.kind_id for a in agents} == {"cursor", "codex", "devin-cli"}


_AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/{}/plugin.schema.json"


@pytest.mark.parametrize(
    ("version", "kinds"), [("1.0.0", {"cursor", "devin-cli"}), ("1.1.0", {"devin-cli"})]
)
def test_an_agent_plugins_manifest_declares_every_kind_that_reads_it(tmp_path, version, kinds):
    """ADR-0074: the portable manifest is nobody's own file, so it is evidence
    for every kind that loads it -- Cursor at the version it supports, Devin
    at any Agent Plugins version, read best-effort."""
    from tools.agent_kinds import DiscoveryContext, discover_agents

    _write(
        tmp_path / "p" / "plugin.json",
        json.dumps({"$schema": _AGENT_PLUGINS_SCHEMA.format(version), "name": "p"}),
    )

    agents = discover_agents(DiscoveryContext(source="declared", scan_root=tmp_path))

    assert {a.kind_id for a in agents} == kinds


def test_a_plugin_json_devin_does_not_read_is_not_evidence(tmp_path):
    """Another tool's schema is no plugin; and a root `plugin.json` beside a
    `.claude-plugin` manifest is not the one Devin reads -- the Claude
    manifest wins, and it is another runtime's own file (ADR-0052)."""
    _write(
        tmp_path / "grafana" / "plugin.json",
        json.dumps({"$schema": "https://example.com/grafana/plugin.schema.json", "name": "g"}),
    )
    shadowed = tmp_path / "shadowed"
    _write(
        shadowed / "plugin.json",
        json.dumps({"$schema": _AGENT_PLUGINS_SCHEMA.format("1.0.0"), "name": "s"}),
    )
    _write(shadowed / ".claude-plugin" / "plugin.json", json.dumps({"name": "s"}))
    # A Claude manifest carrying an Agent Plugins `$schema` is still the Claude
    # candidate of its outer root, never a portable root of its own.
    _write(
        tmp_path / "claude-only" / ".claude-plugin" / "plugin.json",
        json.dumps({"$schema": _AGENT_PLUGINS_SCHEMA.format("1.0.0"), "name": "c"}),
    )

    assert devin_cli.declared_evidence(tmp_path) is None


# --- Roots -----------------------------------------------------------------------


def test_the_config_root_defaults_under_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert devin_cli.resolve_config_root() == tmp_path / ".config" / "devin"
    assert devin_cli.resolve_data_root() == tmp_path / ".local" / "share" / "devin" / "cli"
    assert devin_cli.resolve_legacy_config_root() == tmp_path / ".config" / "cognition"


def test_xdg_variables_move_the_config_and_data_roots_independently(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert devin_cli.resolve_config_root() == tmp_path / "cfg" / "devin"
    assert devin_cli.resolve_legacy_config_root() == tmp_path / "cfg" / "cognition"
    assert devin_cli.resolve_data_root() == tmp_path / "data" / "devin" / "cli"


def test_a_relative_xdg_value_is_ignored(tmp_path, monkeypatch):
    """The XDG base-directory specification treats a relative value as
    invalid, so the default applies."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/cfg")
    monkeypatch.setenv("XDG_DATA_HOME", "")

    assert devin_cli.resolve_config_root() == tmp_path / ".config" / "devin"
    assert devin_cli.resolve_data_root() == tmp_path / ".local" / "share" / "devin" / "cli"


def test_config_dir_is_refused():
    """ADR-0071: four independent root groups, so no single directory names
    them all."""
    assert devin_cli.KIND.root_override_refusal


def test_an_empty_installed_root_still_yields_an_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "devin").mkdir()

    agents = devin_cli.discover(DiscoveryContext(source="installed"))

    assert [a.kind_id for a in agents] == ["devin-cli"]
    assert agents[0].config_root == tmp_path / "devin"


def test_a_missing_installed_root_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert devin_cli.discover(DiscoveryContext(source="installed")) == []


def test_coverage_baseline_is_partial_at_both_sources():
    assert devin_cli.COVERAGE_BASELINE == {"installed": "partial", "declared": "partial"}


def test_the_kind_is_a_registered_singleton():
    from tools.agent_kinds import REGISTRY, kind_for

    assert kind_for("devin-cli") is devin_cli.KIND
    assert devin_cli.KIND in REGISTRY
    assert devin_cli.KIND.cardinality == "singleton"
    assert devin_cli.KIND.display_name == "Devin CLI"


def test_every_rule_that_applies_to_devin_is_allowlisted():
    from tools.posture import KNOWN_RULE_IDS
    from tools.posture.rules import api_endpoint_override, project_trust

    assert devin_cli.KIND.posture_rules == KNOWN_RULE_IDS - {
        project_trust.RULE_ID,
        api_endpoint_override.RULE_ID,
    }


def test_compose_passes_every_resolved_root(tmp_path, monkeypatch):
    """The kind resolves the roots; the builder never reads the environment."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    config = tmp_path / ".config" / "devin"
    config.mkdir(parents=True)
    _write(
        tmp_path / "data" / "devin" / "cli" / "plugins" / "lock.json",
        json.dumps({"resolved": [{"name": "review-tools"}]}),
    )
    (agent,) = devin_cli.discover(DiscoveryContext(source="installed"))

    graph = devin_cli.KIND.compose(agent)

    assert [n.ref.name for n in graph.nodes.values() if n.kind == "plugin" and n.ref] == [
        "review-tools"
    ]


# --- The manifest registry agrees with composition --------------------------------


def _counts(scan_root: Path) -> tuple[int, int]:
    from tools.parsers import parse_repo_grouped

    grouped, found = parse_repo_grouped(
        scan_root, registry=devin_cli.KIND.manifest_patterns, surface=devin_cli.KIND.repo_surface
    )
    return found, found - len(grouped)


def test_a_switched_off_import_is_not_a_source_unit(tmp_path):
    """A file Devin does not read must neither count nor fail."""
    _write(tmp_path / ".mcp.json", "{not json")
    _skill(tmp_path / ".claude" / "skills" / "s")
    assert _counts(tmp_path) == (2, 1)

    _write(tmp_path / ".devin" / "config.json", json.dumps({"read_config_from": {"claude": False}}))
    assert _counts(tmp_path) == (1, 0)


def test_a_malformed_devin_file_is_a_failed_source_unit(tmp_path):
    _write(tmp_path / ".devin" / "mcp_config.json", "{")
    _write(tmp_path / ".devin" / "hooks.v1.json", '{"Stop": "not an array"}')

    assert _counts(tmp_path) == (2, 2)


def test_jsonc_devin_files_parse_cleanly(tmp_path):
    _write(
        tmp_path / ".devin" / "config.json",
        '{\n  // comment\n  "permissions": {"allow": ["Exec(git)",]},\n}\n',
    )

    assert _counts(tmp_path) == (1, 0)


def test_a_directory_subagent_is_one_source_unit(tmp_path):
    _write(tmp_path / ".devin" / "agents" / "researcher" / "AGENT.md", "A\n")
    _write(tmp_path / ".devin" / "agents" / "researcher" / "agents.md", "B\n")

    assert _counts(tmp_path) == (1, 0)


def test_a_plugin_root_with_two_manifests_is_one_source_unit(tmp_path):
    _write(tmp_path / "p" / ".devin-plugin" / "plugin.json", json.dumps({"name": "p"}))
    _write(tmp_path / "p" / ".claude-plugin" / "plugin.json", "{broken")

    assert _counts(tmp_path) == (1, 0)


@pytest.mark.parametrize(
    "manifest",
    ['{"name": "Bad_Name"}', '{"name": "p", "skills": ["../outside"]}', "{broken", "[]"],
)
def test_a_devin_manifest_devin_rejects_is_a_failed_source_unit(tmp_path, manifest):
    """Devin reads `.devin-plugin/plugin.json` whenever it is present and,
    rejecting it, reads no other manifest at that root: it counts and fails,
    while the Claude manifest beside it is not read and does neither."""
    _write(tmp_path / "p" / ".devin-plugin" / "plugin.json", manifest)
    _write(tmp_path / "p" / ".claude-plugin" / "plugin.json", json.dumps({"name": "claude-name"}))

    assert _counts(tmp_path) == (1, 1)


def test_an_agent_plugins_manifest_of_an_unrecognized_version_is_a_parsed_source_unit(tmp_path):
    """Devin loads it best-effort, so it counts and parses rather than fails."""
    _write(
        tmp_path / "p" / "plugin.json",
        json.dumps(
            {"$schema": "https://agent-plugins.org/schemas/1.1.0/plugin.schema.json", "name": "p"}
        ),
    )

    assert _counts(tmp_path) == (1, 0)


def test_nested_skills_devin_does_not_load_are_not_source_units(tmp_path):
    """Neither Devin's own roots nor the Claude and Copilot imports load a
    skill nested a folder deeper (the binary's `devin skills list`)."""
    _skill(tmp_path / ".devin" / "skills" / "group" / "nested")
    _skill(tmp_path / ".claude" / "skills" / "group" / "nested")
    _skill(tmp_path / ".github" / "skills" / "group" / "nested")

    assert _counts(tmp_path) == (0, 0)


# --- Posture through the kind's own surfaces -----------------------------------------


def test_a_declared_scan_reports_both_permission_rules_through_the_kind(tmp_path):
    from tools.agent_kinds import build_agent_graph
    from tools.posture import run_posture_rules

    _write(
        tmp_path / ".devin" / "mcp_config.json",
        json.dumps({"mcpServers": {"github": {"command": "npx", "args": ["-y", "gh-mcp@1.0.0"]}}}),
    )
    _write(
        tmp_path / ".devin" / "config.json",
        json.dumps({"permissions": {"allow": ["Exec(git push)", "mcp__github__*", "Read(**)"]}}),
    )
    (agent,) = _declared(tmp_path)
    graph = build_agent_graph(agent)
    refs = [
        replace(node.ref, extra={**(node.ref.extra or {}), "bom_ref": key})
        for key, node in graph.nodes.items()
        if node.ref is not None
    ]
    assert devin_cli.KIND.posture_manifest_collectors is not None
    mcp_collector, settings_collector = devin_cli.KIND.posture_manifest_collectors

    findings = run_posture_rules(
        refs,
        mcp_collector([tmp_path], refs=refs),
        settings_collector([tmp_path], refs=refs),
        allowed_rules=devin_cli.KIND.posture_rules,
        agent_kind="devin-cli",
    )
    by_rule = {f.rule_id: f for f in findings}

    assert set(by_rule) == {
        "openaca-posture-command-policy-allow",
        "openaca-posture-mcp-auto-approve",
    }
    # The approval names a composed server, so it attaches to that server's
    # bom-ref and a policy gate can act on it.
    assert by_rule["openaca-posture-mcp-auto-approve"].bom_ref is not None


def test_every_allowlisted_rule_fires_on_devins_own_surfaces(tmp_path):
    """Each rule the kind allowlists reaches a Devin-owned file through the
    kind's own collectors — none is allowlisted but unreachable."""
    from click.testing import CliRunner

    from tools.scan import main as scan_main

    _write(
        tmp_path / ".devin" / "mcp_config.json",
        json.dumps(
            {
                "mcpServers": {
                    "unpinned": {"command": "npx", "args": ["-y", "some-mcp-server"]},
                    "plain": {"url": "http://insecure.example.test/mcp"},
                    "literal": {
                        "url": "https://remote.example.test/mcp",
                        "headers": {"Authorization": "Bearer dummy-literal-token"},
                    },
                }
            }
        ),
    )
    _write(
        tmp_path / ".devin" / "skills" / "release" / "SKILL.md",
        "---\nname: release\nallowed-tools:\n  - exec\n---\nShip.\n",
    )
    _write(
        tmp_path / ".devin" / "config.json",
        json.dumps({"permissions": {"allow": ["Exec(git push)", "mcp__unpinned__*"]}}),
    )

    result = CliRunner().invoke(
        scan_main,
        ["repo", "--target", str(tmp_path), "--include-posture", "--format", "json"],
    )

    assert result.exit_code in (0, 1), result.output
    findings = json.loads(result.stdout)["findings"]
    fired = {f["rule_id"] for f in findings if f.get("agent", {}).get("kind") == "devin-cli"}
    assert fired == devin_cli.KIND.posture_rules
    assert "dummy-literal-token" not in result.output
