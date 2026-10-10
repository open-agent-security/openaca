"""Posture rule: flag shell-command prefixes approved to run unattended.

Codex records these in `<root>/rules/*.rules` as a small DSL:

    prefix_rule(pattern=["git", "commit"], decision="allow")

Devin CLI records them as `permissions.allow` entries, `Exec(git commit)` or
the bare tool name `exec` for every command (ADR-0073).

This is **not** `mcp_auto_approve`. That rule reports an MCP *server* running
tools without approval; this reports a *shell command* running without
approval. They share the word "approval" and nothing else, and `rule_id` is a
policy-gate key — `policy_cli` fails a finding whose id is absent from
`risk_gates.posture_rule_ids` — so sharing one id would let a team that
approved vetted MCP auto-run silently also approve unattended `git commit`.
"""

from __future__ import annotations

from pathlib import Path

from tools.posture import devin_permissions
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
    *,
    agent_kind: str | None = None,
) -> list[PostureFinding]:
    """One finding per `decision="allow"` rule.

    `manifests` carries `{"rules": [PrefixRule, ...]}` per file, produced by
    `tools.posture.collect_codex_rules_manifests`. A rule form the parser could
    not read never reaches here — `codex_rules` skips and counts it rather than
    guessing — so this layer inherits that conservatism instead of
    reinterpreting skipped content. A Devin CLI permissions view reports its
    effective `Exec(…)` allows instead.
    """
    findings: list[PostureFinding] = []
    seen: set[tuple[str, str]] = set()
    for path, manifest in manifests:
        devin = manifest.get(devin_permissions.MANIFEST_KEY)
        if isinstance(devin, devin_permissions.DevinPermissions):
            findings.extend(_check_devin_permissions(devin, agent_kind=agent_kind))
            continue
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
                    active_in=[agent_kind or "codex"],
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[{"type": "command_policy", "name": label}],
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                )
            )
    return findings


def _check_devin_permissions(
    permissions: devin_permissions.DevinPermissions, *, agent_kind: str | None = None
) -> list[PostureFinding]:
    """Devin CLI's `Exec(…)` and `exec` allows, already merged across levels
    with denies and higher-precedence asks applied. One finding per prefix per
    file, so each names the file a reader edits; bare `exec` reports as `*`."""
    findings: list[PostureFinding] = []
    seen: set[tuple[str, Path]] = set()
    for allow in permissions.allows:
        words = devin_permissions.exec_prefix(allow.entry)
        if words is None:
            continue
        label = " ".join(words) or "*"
        if (label, allow.path) in seen:
            continue
        seen.add((label, allow.path))
        findings.append(
            PostureFinding(
                rule_id=RULE_ID,
                title=TITLE,
                severity="medium",
                confidence=CONFIDENCE,
                component={"type": "command_policy", "name": label},
                active_in=[agent_kind or "devin"],
                declared_by={"kind": "permissions", "path": str(allow.path)},
                component_path=[{"type": "command_policy", "name": label}],
                standards=_STANDARDS,
                remediation=REMEDIATION,
                evidence={"permission": allow.entry},
            )
        )
    return findings
