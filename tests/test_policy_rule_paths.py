"""Rule references follow the evaluated subject and the reason for its block."""

from tools.component_ref import ComponentRef
from tools.graph import Edge, Graph, Node
from tools.policy import EndpointComponent, apply_risk_gates, evaluate_admission, parse


def policy(**gates):
    return parse(
        {
            "version": 1,
            "admission": {
                "mcps": {"default": "allowed"},
                "plugins": {
                    "default": "allowed",
                    "allowed": [{"plugin": "discord@official"}],
                    "blocked": [{"marketplace": "https://github.com/example/official"}],
                },
                "skills": {"default": "blocked"},
            },
            "risk_gates": gates,
        }
    )


def test_matching_block_rule_takes_precedence_over_allow_rule():
    ref = ComponentRef(
        name="discord",
        extra={
            "component_type": "plugin",
            "marketplace": "official",
            "marketplace_source": "https://github.com/example/official.git",
        },
    )
    decision = evaluate_admission(policy(), [EndpointComponent(ref)])[0]
    assert decision.blocked
    assert decision.rule_paths == ("admission.plugins.blocked[0]",)


def test_default_rule_is_identified_without_a_list_match():
    ref = ComponentRef(name="smoke", extra={"component_type": "skill"})
    decision = evaluate_admission(policy(), [EndpointComponent(ref)])[0]
    assert decision.rule_paths == ("admission.skills.default",)


def test_risk_block_identifies_gate_instead_of_the_admission_allow():
    ref = ComponentRef(name="api", extra={"component_type": "mcp_server"})
    decision = apply_risk_gates(
        policy(posture={"rules": ["openaca-posture-insecure-transport"]}),
        [EndpointComponent(ref)],
        advisories=[],
        advisory_matches=[],
        posture_matches=[(ref, "openaca-posture-insecure-transport")],
    )[0]
    assert decision.blocked
    assert decision.rule_paths == ("risk_gates.posture",)


def test_owned_skill_inherits_the_plugin_block_rule():
    plugin = ComponentRef(
        name="discord",
        extra={
            "component_type": "plugin",
            "marketplace": "official",
            "marketplace_source": "https://github.com/example/official",
        },
    )
    skill = ComponentRef(name="send", extra={"component_type": "skill"})
    graph = Graph(
        nodes={
            "plugin": Node(key="plugin", kind="plugin", ref=plugin),
            "skill": Node(key="skill", kind="skill", ref=skill),
        },
        edges=[Edge(parent="plugin", child="skill")],
    )
    decisions = evaluate_admission(
        policy(), [EndpointComponent(plugin, graph), EndpointComponent(skill, graph)]
    )
    assert decisions[1].controlled_by_plugin
    assert decisions[1].rule_paths == ("admission.plugins.blocked[0]",)


def test_vulnerability_block_identifies_gate():
    ref = ComponentRef(name="api", extra={"component_type": "mcp_server"})
    decision = apply_risk_gates(
        policy(vulnerabilities={"severity_at_least": "high"}),
        [EndpointComponent(ref)],
        advisories=[{"id": "CVE-2026-1234", "database_specific": {"severity": "HIGH"}}],
        advisory_matches=[(ref, "CVE-2026-1234")],
        posture_matches=[],
    )[0]
    assert decision.blocked
    assert decision.rule_paths == ("risk_gates.vulnerabilities",)
