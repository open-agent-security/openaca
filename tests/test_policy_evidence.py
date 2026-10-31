from pathlib import Path

import pytest

from tools import policy_compile
from tools.component_ref import ComponentRef
from tools.policy import EndpointComponent, PolicyEvaluationError, parse


def _policy(*, vulnerabilities: bool = False):
    document = {
        "version": 1,
        "admission": {name: {"default": "allowed"} for name in ("mcps", "plugins", "skills")},
    }
    if vulnerabilities:
        document["risk_gates"] = {"vulnerabilities": {"severity_at_least": "high"}}
    return parse(document)


def test_collection_returns_domain_inputs_without_compiling_host_settings(monkeypatch):
    ref = ComponentRef(name="deploy", extra={"type": "skill"})
    components = [EndpointComponent(ref)]
    findings = [(ref, "CVE-2026-1234")]
    advisory = {"id": "CVE-2026-1234"}
    seen = []

    def collect(policy, target, project, *, agent_kind, allow_partial_inventory):
        seen.append((policy, target, project, allow_partial_inventory))
        return components, findings, [advisory], [(ref, "rule")], ["unmapped"], []

    def no_render(*args, **kwargs):
        pytest.fail("evidence collection must not compile host settings")

    monkeypatch.setattr(policy_compile, "_evaluate_endpoint", collect)
    monkeypatch.setattr(policy_compile, "compile_policy", no_render)
    policy = _policy()
    result = policy_compile.collect_policy_evidence(
        policy, target=Path("/config"), project=Path("/project")
    )
    assert seen == [(policy, Path("/config"), Path("/project"), False)]
    assert result.components == tuple(components)
    assert result.advisory_matches == tuple(findings)
    assert result.advisories == (advisory,)
    assert result.posture_matches == ((ref, "rule"),)
    assert result.unmapped_posture == ("unmapped",)
    assert result.vulnerability_unavailable is None
    assert result.inventory_gaps == ()
    components.clear()
    findings.clear()
    assert len(result.components) == len(result.advisory_matches) == 1


def test_unqueryable_coordinates_are_not_a_clean_vulnerability_result(monkeypatch):
    queryable = ComponentRef(ecosystem="PyPI", name="example", version="1.0")
    unavailable = ComponentRef(name="local-skill")
    monkeypatch.setattr(
        policy_compile,
        "_evaluate_endpoint",
        lambda *args, **kwargs: (
            [EndpointComponent(queryable), EndpointComponent(unavailable)],
            [],
            [],
            [],
            [],
            [],
        ),
    )
    result = policy_compile.collect_policy_evidence(
        _policy(vulnerabilities=True), target=Path("/config"), project=None
    )
    assert result.vulnerability_unavailable == (unavailable,)


def test_failed_collection_propagates_instead_of_returning_empty_clean_evidence(monkeypatch):
    def fail(*args, **kwargs):
        raise PolicyEvaluationError("incomplete inventory")

    monkeypatch.setattr(policy_compile, "_evaluate_endpoint", fail)
    with pytest.raises(PolicyEvaluationError, match="incomplete inventory"):
        policy_compile.collect_policy_evidence(_policy(), target=Path("/config"), project=None)


def test_default_root_collection_does_not_force_an_explicit_configuration_directory(monkeypatch):
    seen = []

    def collect(policy, target, project, *, agent_kind, allow_partial_inventory):
        seen.append((target, project))
        return [], [], [], [], [], []

    monkeypatch.setattr(policy_compile, "_evaluate_endpoint", collect)
    policy_compile.collect_policy_evidence(_policy(), project=Path("/project"))
    assert seen == [(None, Path("/project"))]


def test_unknown_agent_kind_fails_before_discovery(monkeypatch):
    def discover(*args):
        pytest.fail("unsupported kind must not discover another host")

    monkeypatch.setattr(policy_compile, "discover_agents", discover)
    with pytest.raises(PolicyEvaluationError, match="unsupported policy evidence agent kind"):
        policy_compile.collect_policy_evidence(_policy(), project=None, agent_kind="unknown")


def test_cursor_is_a_policy_evidence_kind_with_its_fixed_root(monkeypatch, tmp_path):
    seen = []

    def collect(policy, target, project, *, agent_kind, allow_partial_inventory):
        seen.append((target, agent_kind))
        return [], [], [], [], [], []

    monkeypatch.setattr(policy_compile.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(policy_compile, "_evaluate_endpoint", collect)
    policy_compile.collect_policy_evidence(_policy(), project=None, agent_kind="cursor")
    policy_compile.collect_policy_evidence(
        _policy(), target=tmp_path / ".cursor", project=None, agent_kind="cursor"
    )
    assert seen == [(None, "cursor"), (tmp_path / ".cursor", "cursor")]


def test_a_relocated_cursor_root_fails_before_discovery(monkeypatch, tmp_path):
    def discover(*args):
        pytest.fail("a relocated Cursor root must not discover the home directory's Cursor")

    monkeypatch.setattr(policy_compile.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(policy_compile, "discover_agents", discover)
    with pytest.raises(PolicyEvaluationError, match="no relocatable configuration root"):
        policy_compile.collect_policy_evidence(
            _policy(), target=tmp_path / "elsewhere", project=None, agent_kind="cursor"
        )


def _devin_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    root = tmp_path / ".config/devin"
    root.mkdir(parents=True)
    return root


def test_devin_is_a_policy_evidence_kind_with_its_fixed_root(monkeypatch, tmp_path):
    seen = []

    def collect(policy, target, project, *, agent_kind, allow_partial_inventory):
        seen.append((target, agent_kind))
        return [], [], [], [], [], []

    root = _devin_home(monkeypatch, tmp_path)
    monkeypatch.setattr(policy_compile, "_evaluate_endpoint", collect)
    assert "devin" in policy_compile.POLICY_EVIDENCE_AGENT_KINDS
    policy_compile.collect_policy_evidence(_policy(), project=None, agent_kind="devin")
    policy_compile.collect_policy_evidence(_policy(), target=root, project=None, agent_kind="devin")
    assert seen == [(None, "devin"), (root, "devin")]


def test_devin_root_follows_xdg_config_home(monkeypatch, tmp_path):
    seen = []

    def collect(policy, target, project, *, agent_kind, allow_partial_inventory):
        seen.append(target)
        return [], [], [], [], [], []

    _devin_home(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(policy_compile, "_evaluate_endpoint", collect)
    policy_compile.collect_policy_evidence(
        _policy(), target=tmp_path / "xdg/devin", project=None, agent_kind="devin"
    )
    assert seen == [tmp_path / "xdg/devin"]
    with pytest.raises(PolicyEvaluationError, match="no relocatable configuration root"):
        policy_compile.collect_policy_evidence(
            _policy(), target=tmp_path / ".config/devin", project=None, agent_kind="devin"
        )


def test_a_relocated_devin_root_fails_before_discovery(monkeypatch, tmp_path):
    def discover(*args):
        pytest.fail("a relocated Devin root must not discover the home directory's Devin")

    _devin_home(monkeypatch, tmp_path)
    monkeypatch.setattr(policy_compile, "discover_agents", discover)
    with pytest.raises(PolicyEvaluationError, match="devin policy evidence has no relocatable"):
        policy_compile.collect_policy_evidence(
            _policy(), target=tmp_path / "elsewhere", project=None, agent_kind="devin"
        )


def test_a_devin_store_plugin_is_an_inventory_gap(monkeypatch, tmp_path):
    _devin_home(monkeypatch, tmp_path)
    lock = tmp_path / ".local/share/devin/cli/plugins/lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text('{"requirements": [], "resolved": [{"name": "review-tools"}], "edges": []}')
    with pytest.raises(PolicyEvaluationError, match="contents of Devin plugin review-tools"):
        policy_compile.collect_policy_evidence(_policy(), project=None, agent_kind="devin")
    evidence = policy_compile.collect_policy_evidence(
        _policy(), project=None, agent_kind="devin", allow_partial_inventory=True
    )
    assert evidence.inventory_gaps == (
        f"{lock}: $.resolved[0]: contents of Devin plugin review-tools are not inventoried",
    )
    assert [c.ref.name for c in evidence.components] == ["review-tools"]


def test_devin_without_store_plugins_has_no_inventory_gap(monkeypatch, tmp_path):
    root = _devin_home(monkeypatch, tmp_path)
    (root / "mcp_config.json").write_text('{"mcpServers": {"fixture": {"command": "fixture"}}}')
    evidence = policy_compile.collect_policy_evidence(_policy(), project=None, agent_kind="devin")
    assert evidence.inventory_gaps == ()
    assert len(evidence.components) == 1
