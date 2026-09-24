"""Pi's file-declared composition (ADR-0067)."""

from __future__ import annotations

import os
from pathlib import Path

from tools.agent_kinds import AgentInstance, AgentKind, DiscoveryContext
from tools.parsers import PI_MANIFEST_REGISTRY
from tools.parsers.pi_manifest import declaration_files
from tools.posture import collect_pi_project_trust_manifests
from tools.posture.rules import mutable_install, project_trust
from tools.repo_surface import PI_SURFACE

COVERAGE_BASELINE = {"installed": "partial", "declared": "partial"}
ROOT_OVERRIDE_REFUSAL = (
    "Pi also reads ~/.agents/skills, which remains home-scoped when its agent "
    "directory is relocated; --config-dir cannot specify this complete target"
)


def resolve_config_root(config_dir: Path | None = None) -> Path:
    value = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(value).expanduser().absolute() if value else Path.home() / ".pi/agent"


def declared_evidence(scan_root: Path, *, include_gitignored: bool = False) -> Path | None:
    return next(iter(declaration_files(scan_root, include_gitignored=include_gitignored)), None)


def discover(ctx: DiscoveryContext) -> list[AgentInstance]:
    root = resolve_config_root() if ctx.source == "installed" else ctx.scan_root
    if root is None or not root.is_dir():
        return []
    if (
        ctx.source == "declared"
        and declared_evidence(root, include_gitignored=ctx.include_gitignored) is None
    ):
        return []
    return [
        AgentInstance(
            kind_id="pi",
            display_name="Pi",
            source=ctx.source,
            root_label="pi",
            coverage_baseline=COVERAGE_BASELINE[ctx.source],
            config_root=root if ctx.source == "installed" else None,
            scan_root=root if ctx.source == "declared" else None,
            project_root=ctx.project_root,
        )
    ]


def _compose(agent, *, include_gitignored=False, warnings=None):
    from tools.graph_build_pi import build_pi_graph

    return build_pi_graph(agent, include_gitignored=include_gitignored, warnings=warnings)


KIND = AgentKind(
    id="pi",
    display_name="Pi",
    cardinality="singleton",
    root_label="pi",
    coverage_baseline=COVERAGE_BASELINE,
    discover=discover,
    compose=_compose,
    root_override_refusal=ROOT_OVERRIDE_REFUSAL,
    posture_rules=frozenset({mutable_install.RULE_ID, project_trust.RULE_ID}),
    manifest_patterns=tuple(PI_MANIFEST_REGISTRY),
    repo_surface=PI_SURFACE,
    extra_installed_posture_collectors={project_trust.RULE_ID: collect_pi_project_trust_manifests},
)
