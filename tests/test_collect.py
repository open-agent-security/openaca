"""Unit tests for `tools/collect.py` — the collection API.

The fixture shapes below are this module's own.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tools.agent_kinds import AgentInstance, DiscoveryContext, discover_agents
from tools.collect import (
    CollectedAgent,
    ScannerUnavailable,
    collect_for_agent,
    collect_installed_agents,
)
from tools.observations.finding import ObservationFinding
from tools.posture.finding import PostureFinding


def _endpoint_fixture(root: Path) -> Path:
    skill = root / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    (root / ".mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh"]}}}',
        encoding="utf-8",
    )
    return root


def _claude_agent(config_dir: Path, project: Path | None = None) -> AgentInstance:
    agents = discover_agents(
        DiscoveryContext(
            source="installed", config_dir=config_dir, project_root=project, kind_id="claude-code"
        )
    )
    assert len(agents) == 1
    return agents[0]


def _codex_root_with_approvals(tmp_path: Path) -> Path:
    """A Codex config root carrying both approval surfaces."""
    root = tmp_path / ".codex"
    (root / "rules").mkdir(parents=True)
    (root / "rules" / "default.rules").write_text(
        'prefix_rule(pattern=["uv", "sync"], decision="allow")\n',
        encoding="utf-8",
    )
    (root / "config.toml").write_text(
        '[projects."/home/u/work/repo"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    return root


def test_collect_for_agent_returns_the_finding_dataclasses_not_payload_dicts(tmp_path):
    """A dict-shaped result is the specific regression the returns-itself
    decision exists to prevent: the removed uploader mapped `rule_id` to
    `finding_id`, `title` to `summary` and `remediation` to `fix` inside the
    collection step, and that vocabulary is its server's, not OpenACA's."""
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collected = collect_for_agent(_claude_agent(config_dir))

    assert isinstance(collected, CollectedAgent)
    assert collected.posture_findings
    for finding in collected.posture_findings:
        assert isinstance(finding, PostureFinding)
        assert not isinstance(finding, dict)
    for observation in collected.observations:
        assert isinstance(observation, ObservationFinding)
        assert not isinstance(observation, dict)


def test_collect_for_agent_stamps_the_agent_on_every_finding(tmp_path):
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collected = collect_for_agent(_claude_agent(config_dir))

    assert collected.agent_kind == "claude-code"
    assert collected.agent_id is None
    assert collected.config_root == config_dir
    for finding in (*collected.posture_findings, *collected.observations):
        assert finding.agent_kind == "claude-code"
        assert finding.agent_id is None


def test_scanner_produced_findings_are_stamped_with_the_agent_too(tmp_path, monkeypatch):
    from tools.observations.skillspector import SkillSpectorFindings

    config_dir = _endpoint_fixture(tmp_path / ".claude")
    monkeypatch.setattr(
        "tools.collect.collect_skillspector_findings",
        lambda refs: SkillSpectorFindings(
            observations=[
                ObservationFinding(
                    source="skillspector",
                    source_version="0.4.0",
                    observation_id="P1",
                    title="Instruction override",
                    severity="high",
                    confidence="medium",
                    component={"identity": "skill/deploy", "name": "deploy", "type": "skill"},
                    subject_coordinate="sha256:test",
                )
            ],
            posture_findings=[_scanner_posture("LP2")],
            warnings=[],
        ),
    )

    collected = collect_for_agent(
        _claude_agent(config_dir), external_scanners=("nvidia-skillspector",)
    )

    scanner_posture = [f for f in collected.posture_findings if f.source == "skillspector"]
    scanner_observations = [o for o in collected.observations if o.source == "skillspector"]
    assert scanner_posture and scanner_observations
    for finding in (*scanner_posture, *scanner_observations):
        assert finding.agent_kind == "claude-code"


def _scanner_posture(rule_id: str, source: str = "skillspector") -> PostureFinding:
    from tools.posture.finding import Standards

    return PostureFinding(
        source=source,
        source_version="0.4.0",
        rule_id=rule_id,
        title="Wildcard permission",
        severity="medium",
        confidence="medium",
        component={"identity": "skill/deploy", "name": "deploy", "type": "skill"},
        active_in=[],
        declared_by={"kind": "sarif", "path": "skills/deploy/SKILL.md"},
        component_path=[{"type": "skill", "name": "skill/deploy"}],
        standards=Standards(),
        remediation="Review the declared permission.",
    )


def test_component_count_matches_the_bom(tmp_path):
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collected = collect_for_agent(_claude_agent(config_dir))

    assert collected.component_count == len(collected.bom["components"])
    assert collected.component_count > 0


def test_include_target_defaults_to_naming_the_agents_own_root(tmp_path):
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collected = collect_for_agent(_claude_agent(config_dir))

    props = {p["name"]: p["value"] for p in collected.bom["metadata"]["properties"]}
    assert props["openaca:target"] == str(config_dir)


def test_include_target_false_names_no_place(tmp_path):
    """A consumer shipping the document elsewhere must not carry an absolute
    local path with it."""
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collected = collect_for_agent(_claude_agent(config_dir), include_target=False)

    props = {p["name"]: p["value"] for p in collected.bom["metadata"]["properties"]}
    assert "openaca:target" not in props
    assert "openaca:target_type" not in props


def test_warnings_carry_a_malformed_manifest_note_and_lower_the_coverage(tmp_path):
    """The `evidence_gaps=_component_gap_count(warnings)` wiring must survive
    the move, or a partially-composed agent is reported as `complete`."""
    config_dir = _endpoint_fixture(tmp_path / ".claude")
    (config_dir / ".mcp.json").write_text("{not json", encoding="utf-8")

    collected = collect_for_agent(_claude_agent(config_dir))

    assert collected.warnings
    assert any("mcp.json" in w for w in collected.warnings)
    component_props = {
        p["name"]: p["value"] for p in collected.bom["metadata"]["component"]["properties"]
    }
    assert component_props["openaca:composition_coverage"] != "complete"


def test_no_posture_rule_is_filtered(tmp_path):
    """Which findings a consumer chooses to publish is that consumer's
    decision. The library reports what the scan found, exactly as
    `openaca scan` does, and filters nothing."""
    root = _codex_root_with_approvals(tmp_path)
    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=root, project_root=None, kind_id="codex")
    )

    collected = collect_for_agent(agents[0])

    assert "openaca-posture-command-policy-allow" in {f.rule_id for f in collected.posture_findings}


def test_missing_external_scanner_raises_scanner_unavailable(tmp_path, monkeypatch):
    from tools.observations.skillspector import SkillSpectorCommandNotFound

    config_dir = _endpoint_fixture(tmp_path / ".claude")

    def missing(_refs):
        raise SkillSpectorCommandNotFound("SkillSpector command not found: skillspector")

    monkeypatch.setattr("tools.collect.collect_skillspector_findings", missing)

    with pytest.raises(ScannerUnavailable) as excinfo:
        collect_for_agent(_claude_agent(config_dir), external_scanners=("nvidia-skillspector",))

    assert str(excinfo.value) == "SkillSpector command not found: skillspector"
    assert isinstance(excinfo.value.__cause__, SkillSpectorCommandNotFound)
    assert not hasattr(excinfo.value, "exit_code")


def test_a_kind_that_resolves_no_config_root_is_a_value_error():
    """An impossible-invariant guard against a bug in a kind module, not an
    operational failure a caller handles — so `ValueError`, not a second
    public error on a surface the spec fixes at one."""
    agent = AgentInstance(
        kind_id="claude-code",
        display_name="Claude Code",
        source="installed",
        root_label="config-dir",
        coverage_baseline="complete",
        config_root=None,
    )

    with pytest.raises(ValueError, match="resolved no config_root"):
        collect_for_agent(agent)


# --- the public entry point --------------------------------------------------


def test_collect_installed_agents_returns_one_result_per_discovered_agent(tmp_path):
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collected = collect_installed_agents(config_dir=config_dir, kind_id="claude-code")

    assert [c.agent_kind for c in collected] == ["claude-code"]
    assert isinstance(collected[0], CollectedAgent)
    # `include_target` defaults to True, so the CLI paths are unchanged.
    props = {p["name"]: p["value"] for p in collected[0].bom["metadata"]["properties"]}
    assert props["openaca:target"] == str(config_dir)


def test_collect_installed_agents_is_keyword_only():
    signature = inspect.signature(collect_installed_agents)
    assert [p.kind for p in signature.parameters.values()] == [
        inspect.Parameter.KEYWORD_ONLY
    ] * len(signature.parameters)
    assert set(signature.parameters) == {
        "config_dir",
        "project",
        "kind_id",
        "external_scanners",
        "include_target",
    }
    assert signature.parameters["include_target"].default is True


def test_zero_discovered_agents_returns_an_empty_sequence(tmp_path):
    """ "Nothing is installed here" is an answer, not a failure: the caller
    gets an empty sequence rather than an exception."""
    assert collect_installed_agents(config_dir=tmp_path / "empty", kind_id="claude-code") == []


def test_an_unrecognised_external_scanner_entry_is_ignored(tmp_path):
    """`_collect_scanner_findings` tests membership and never enumerates the
    argument. Pinned so a later tidy-up that starts raising on unknown names
    is a visible change rather than a silent one."""
    config_dir = _endpoint_fixture(tmp_path / ".claude")

    collected = collect_installed_agents(
        config_dir=config_dir, kind_id="claude-code", external_scanners=("no-such-scanner",)
    )

    assert len(collected) == 1
    assert collected[0].component_count > 0


# --- the error boundary: the library names the failure and nothing else ------


def test_the_published_function_raises_scanner_unavailable(tmp_path, monkeypatch):
    """`ScannerUnavailable` names the failure and carries no `exit_code`,
    because an exit code is a process's concern: a caller that needs one
    decides it for itself."""
    import openaca.core as core
    from tools.observations.skillspector import SkillSpectorCommandNotFound

    config_dir = _endpoint_fixture(tmp_path / ".claude")
    message = "SkillSpector command not found: skillspector"

    def missing(_refs):
        raise SkillSpectorCommandNotFound(message)

    monkeypatch.setattr("tools.collect.collect_skillspector_findings", missing)

    with pytest.raises(core.ScannerUnavailable) as library_exc:
        core.collect_installed_agents(
            config_dir=config_dir,
            kind_id="claude-code",
            external_scanners=("nvidia-skillspector",),
        )
    assert str(library_exc.value) == message


# --- the config_root leak ------------------------------------------------------


def test_each_result_carries_its_own_agents_config_root(tmp_path, monkeypatch):
    """Cursor resolves its root to `<home>/.cursor` and ignores `config_dir`
    entirely (ADR-0054), which makes it the honest fixture for the leak.

    The failure mode this guards is silent and the wrong kind of silent: the
    value that survives a failed relativisation is a bare basename rather than
    an error, so a consumer relativising every agent's paths against the
    `config_dir` argument ships a partially-relativised document and nothing
    raises.
    """
    home = tmp_path / "home"
    (home / ".cursor").mkdir(parents=True)
    (home / ".cursor" / "mcp.json").write_text(
        '{"mcpServers": {"gh": {"command": "npx", "args": ["-y", "@x/gh"]}}}', encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CURSOR_CONFIG_DIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    claude_dir = _endpoint_fixture(tmp_path / "claude")

    collected = collect_installed_agents(config_dir=claude_dir, project=None, kind_id=None)

    roots = {c.agent_kind: c.config_root for c in collected}
    assert roots["claude-code"] == claude_dir
    # A root the `config_dir` argument cannot express.
    assert roots["cursor"] == home / ".cursor"

    for result in collected:
        sources = _component_source_paths(result.bom)
        assert sources
        for source in sources:
            # Relativising against the result's own root succeeds for both.
            Path(source).relative_to(result.config_root)

    cursor = next(c for c in collected if c.agent_kind == "cursor")
    with pytest.raises(ValueError):
        for source in _component_source_paths(cursor.bom):
            Path(source).relative_to(claude_dir)


def _component_source_paths(bom: dict) -> list[str]:
    paths = []
    for component in bom.get("components") or []:
        for prop in component.get("properties") or []:
            if prop["name"] == "openaca:source_manifest" and prop["value"].startswith("/"):
                paths.append(prop["value"])
    return paths
