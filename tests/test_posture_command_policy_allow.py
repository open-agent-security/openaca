"""`openaca-posture-command-policy-allow` (plan 043 Task 10A).

Its own rule id rather than a reuse of `mcp_auto_approve`: `rule_id` is a
policy-gate key, so sharing one would let a team approving vetted MCP auto-run
silently also approve unattended shell commands.
"""

from __future__ import annotations

from pathlib import Path

from tools.parsers.codex_rules import PrefixRule
from tools.posture.rules.command_policy_allow import RULE_ID, check_command_policy_allow


def _manifest(*rules):
    return [(Path("/x/rules/default.rules"), {"rules": list(rules)})]


def test_an_allow_rule_produces_a_finding():
    findings = check_command_policy_allow(_manifest(PrefixRule(("git", "commit"), "allow")))

    assert len(findings) == 1
    assert findings[0].rule_id == RULE_ID
    assert findings[0].component["name"] == "git commit"


def test_a_deny_rule_produces_nothing():
    assert check_command_policy_allow(_manifest(PrefixRule(("rm", "-rf"), "deny"))) == []


def test_unparsed_content_produces_neither_a_false_allow_nor_a_false_deny():
    """The parser skips-and-counts unrecognised forms rather than guessing, and
    this layer inherits that instead of reinterpreting the skipped content."""
    assert check_command_policy_allow([(Path("/x/rules/default.rules"), {"rules": []})]) == []


def test_duplicate_patterns_are_deduped():
    findings = check_command_policy_allow(
        _manifest(PrefixRule(("git",), "allow"), PrefixRule(("git",), "allow"))
    )

    assert len(findings) == 1


def test_no_manifests_is_no_findings():
    """A declared Codex agent supplies none — by empty input, not a special case."""
    assert check_command_policy_allow([]) == []


def test_the_rule_is_not_tagged_as_an_mcp_exposure():
    findings = check_command_policy_allow(_manifest(PrefixRule(("git",), "allow")))

    assert findings[0].standards.owasp_agentic_top10 == ["asi03"]
    assert not findings[0].standards.owasp_mcp_top10
