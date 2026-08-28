"""Posture rule: flag shell-command prefixes approved to run unattended.

Codex records these in `<root>/rules/*.rules` as a small DSL:

    prefix_rule(pattern=["git", "commit"], decision="allow")

This is **not** `mcp_auto_approve`. That rule reports an MCP *server* running
tools without approval; this reports a *shell command* running without
approval. They share the word "approval" and nothing else, and `rule_id` is a
policy-gate key — `policy_cli` fails a finding whose id is absent from
`risk_gates.posture_rule_ids` — so sharing one id would let a team that
approved vetted MCP auto-run silently also approve unattended `git commit`.
"""

from __future__ import annotations

from pathlib import Path

from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-command-policy-allow"
TITLE = "Shell command runs without approval"
CONFIDENCE = "high"
REMEDIATION = (
    "Review this command-prefix allow rule. A prefix matches every command "
    "starting with it, so a broad prefix approves more than it appears to — "
    "`git` alone permits `git push`, and an allowed prefix ending in an "
    "argument-taking flag permits whatever follows. Narrow the pattern to the "
    "exact invocations you intend, or remove the rule so the command prompts."
)

# ASI03 (agent tool misuse). Deliberately no `owasp_mcp_top10` tag: this is a
# shell-execution exposure, not an MCP one.
_STANDARDS = Standards(owasp_agentic_top10=["asi03"])


def check_command_policy_allow(
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    """One finding per `decision="allow"` rule.

    `manifests` carries `{"rules": [PrefixRule, ...]}` per file, produced by
    `tools.posture.collect_codex_rules_manifests`. A rule form the parser could
    not read never reaches here — `codex_rules` skips and counts it rather than
    guessing — so this layer inherits that conservatism instead of
    reinterpreting skipped content.
    """
    findings: list[PostureFinding] = []
    seen: set[tuple[str, str]] = set()
    for path, manifest in manifests:
        for rule in manifest.get("rules") or []:
            if getattr(rule, "decision", None) != "allow":
                continue
            label = " ".join(rule.pattern)
            if not label or (RULE_ID, label) in seen:
                continue
            seen.add((RULE_ID, label))
            findings.append(
                PostureFinding(
                    rule_id=RULE_ID,
                    title=TITLE,
                    severity="medium",
                    confidence=CONFIDENCE,
                    component={"type": "command_policy", "name": label},
                    active_in=["codex"],
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[{"type": "command_policy", "name": label}],
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                )
            )
    return findings
