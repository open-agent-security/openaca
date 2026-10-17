"""The skills default includes legacy custom slash commands, as the host does."""

from openaca.core import ComponentRef, EndpointComponent, evaluate_admission, parse_policy


def test_legacy_command_uses_skill_admission_default():
    for default in ("allowed", "blocked"):
        policy = parse_policy(
            {
                "version": 1,
                "admission": {name: {"default": default} for name in ("mcps", "plugins", "skills")},
            }
        )
        ref = ComponentRef(name="deploy", extra={"component_type": "command"})
        (decision,) = evaluate_admission(policy, [EndpointComponent(ref)])
        assert decision.category == "skills"
        assert decision.blocked == (default == "blocked")
