"""Posture rule: flag directories marked trusted.

Codex records these in `config.toml`:

    [projects."/Users/u/work/repo"]
    trust_level = "trusted"

Trust is granted per directory, so it is the broadest of Codex's three
approval surfaces — wider than a single command prefix and wider than a single
MCP server. It gets its own `rule_id` for the reason the spec gives: `rule_id`
is a policy-gate key, and a team allowing one approval concern must not
silently allow the others.
"""

from __future__ import annotations

from pathlib import Path

from tools.posture.finding import PostureFinding, Standards

RULE_ID = "openaca-posture-project-trust"
TITLE = "Directory is marked trusted"
CONFIDENCE = "high"
REMEDIATION = (
    "Review whether this directory still warrants trust. A trusted directory "
    "relaxes approval prompts for work inside it, so it carries whatever a "
    "dependency update, a merged branch, or a cloned subdirectory brings in. "
    "Remove the entry to restore prompting, or scope trust to a narrower path."
)

_TRUSTED = "trusted"

_STANDARDS = Standards(owasp_agentic_top10=["asi03"])


def check_project_trust(
    manifests: list[tuple[Path, dict]],
) -> list[PostureFinding]:
    """One finding per project whose `trust_level` is `"trusted"`.

    `manifests` carries `{"projects": {path: trust_level}}`, produced by
    `tools.posture.collect_codex_project_trust_manifests`. Any other
    `trust_level` value — including an absent one — is not a finding: only the
    documented trusted state relaxes prompting, and inventing a meaning for an
    unrecognised value would report a posture the runtime does not have.
    """
    findings: list[PostureFinding] = []
    seen: set[tuple[str, str]] = set()
    for path, manifest in manifests:
        projects = manifest.get("projects") or {}
        for project_path, trust_level in sorted(projects.items()):
            if trust_level != _TRUSTED:
                continue
            if (RULE_ID, project_path) in seen:
                continue
            seen.add((RULE_ID, project_path))
            findings.append(
                PostureFinding(
                    rule_id=RULE_ID,
                    title=TITLE,
                    severity="medium",
                    confidence=CONFIDENCE,
                    component={"type": "project", "name": project_path},
                    active_in=["codex"],
                    declared_by={"kind": "manifest", "path": str(path)},
                    component_path=[{"type": "project", "name": project_path}],
                    standards=_STANDARDS,
                    remediation=REMEDIATION,
                )
            )
    return findings
