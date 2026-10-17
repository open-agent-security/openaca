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

    def collect(policy, target, project):
        seen.append((policy, target, project))
        return components, findings, [advisory], [(ref, "rule")], ["unmapped"]

    def no_render(*args, **kwargs):
        pytest.fail("evidence collection must not compile host settings")

    monkeypatch.setattr(policy_compile, "_evaluate_endpoint", collect)
    monkeypatch.setattr(policy_compile, "compile_policy", no_render)
    policy = _policy()
    result = policy_compile.collect_policy_evidence(
        policy, target=Path("/config"), project=Path("/project")
    )
    assert seen == [(policy, Path("/config"), Path("/project"))]
    assert result.components == tuple(components)
    assert result.advisory_matches == tuple(findings)
    assert result.advisories == (advisory,)
    assert result.posture_matches == ((ref, "rule"),)
    assert result.unmapped_posture == ("unmapped",)
    assert result.vulnerability_unavailable is None
    components.clear()
    findings.clear()
    assert len(result.components) == len(result.advisory_matches) == 1


def test_unqueryable_coordinates_are_not_a_clean_vulnerability_result(monkeypatch):
    queryable = ComponentRef(ecosystem="PyPI", name="example", version="1.0")
    unavailable = ComponentRef(name="local-skill")
    monkeypatch.setattr(
        policy_compile,
        "_evaluate_endpoint",
        lambda *args: (
            [EndpointComponent(queryable), EndpointComponent(unavailable)],
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
    def fail(*args):
        raise PolicyEvaluationError("incomplete inventory")

    monkeypatch.setattr(policy_compile, "_evaluate_endpoint", fail)
    with pytest.raises(PolicyEvaluationError, match="incomplete inventory"):
        policy_compile.collect_policy_evidence(_policy(), target=Path("/config"), project=None)
