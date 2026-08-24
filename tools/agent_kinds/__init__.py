"""Agent kind registry — the generalisation of the flat manifest registry.

A *kind* is what reads a composition (ADR-0044): two runtimes are the same kind
only if they read the same surface with the same schema. A kind declares its
discovery, composition, cardinality, coverage baseline per composition source,
node-key root label, display label, and posture-rule allowlist. Of that, only the
kind id and the resolved coverage reach a BOM.

Discovery returns a *list* of `AgentInstance`. That is the property that cannot be
retrofitted, so it is a list even while every registered kind is a singleton.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tools.bom import AGENT_ROOT_PREFIX
from tools.capability import COVERAGE_LEVELS
from tools.component_ref import ComponentRef
from tools.graph import Graph

Cardinality = Literal["singleton", "many_per_place"]
CompositionSource = Literal["installed", "declared"]
# A kind's declared manifest surface (spec: "It declares discovery,
# composition, its manifest patterns ..."). Same shape as
# `tools.parsers.REGISTRY` — (glob pattern, parser function) — so a kind can
# hand its patterns straight through without translation.
ParserFn = Callable[[Path], list[ComponentRef]]
# `collect_mcp_manifests`/`collect_settings_manifests` take
# `(roots, include_gitignored=...)` and callers pass the second by keyword, which
# a positional-only signature would reject — so this is `...` like its installed
# counterpart below.
PostureManifestCollector = Callable[..., list[tuple[Path, dict]]]
PostureCollectors = tuple[PostureManifestCollector, PostureManifestCollector]
# An installed agent's collectors take (config_root, project_root[, refs]) rather
# than a root list — the shape `collect_endpoint_mcp_manifests`/
# `collect_endpoint_settings_manifests` already have, so this is typed loosely
# rather than forcing both branches through one signature.
InstalledPostureCollector = Callable[..., list[tuple[Path, dict]]]
InstalledPostureCollectors = tuple[InstalledPostureCollector, InstalledPostureCollector]

COMPOSITION_SOURCES: frozenset[str] = frozenset({"installed", "declared"})

_SLUG_UNSAFE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class AgentInstance:
    kind_id: str
    display_name: str
    source: CompositionSource
    root_label: str
    coverage_baseline: str
    config_root: Path | None = None
    project_root: Path | None = None
    scan_root: Path | None = None
    agent_id: str | None = None

    @property
    def bom_ref(self) -> str:
        if self.agent_id is None:
            return f"{AGENT_ROOT_PREFIX}{self.kind_id}"
        return f"{AGENT_ROOT_PREFIX}{self.kind_id}/{self.agent_id}"

    @property
    def output_basename(self) -> str:
        if self.agent_id is None:
            return self.kind_id
        return f"{self.kind_id}--{slugify_agent_id(self.agent_id)}"

    def validate_against(self, kind: AgentKind) -> AgentInstance:
        """Cardinality decides whether a discriminator is required or forbidden.

        A singleton emitting one means discovery is wrong (ADR-0045), which is
        worth failing loudly on rather than shipping in a document.
        """
        if kind.cardinality == "singleton" and self.agent_id is not None:
            raise ValueError(
                f"kind {kind.id!r} is singleton; agent_id must be absent, got {self.agent_id!r}"
            )
        if kind.cardinality == "many_per_place" and not self.agent_id:
            raise ValueError(f"kind {kind.id!r} has same-kind multiplicity; agent_id is required")
        return self


@dataclass(frozen=True)
class DiscoveryContext:
    source: CompositionSource
    config_dir: Path | None = None
    project_root: Path | None = None
    scan_root: Path | None = None
    include_gitignored: bool = False


@dataclass(frozen=True)
class AgentKind:
    id: str
    display_name: str
    cardinality: Cardinality
    root_label: str
    coverage_baseline: Mapping[str, str]
    discover: Callable[[DiscoveryContext], list[AgentInstance]]
    compose: Callable[..., Graph]
    posture_rules: frozenset[str] | None = None
    # The kind's repo-tree manifest surface (spec "Internals not visible in a
    # BOM": a kind declares discovery, composition, "its manifest patterns" —
    # polymorphic per kind shape, so a control-plane kind holds no filesystem
    # fields at all). Empty means the kind resolves its own filesystem lookups
    # entirely inside `compose`/`discover` rather than through this shared
    # declaration.
    manifest_patterns: tuple[tuple[str, ParserFn], ...] = ()
    # (mcp_collector, settings_collector) a *declared* agent's posture prep
    # reads through, instead of `_agent_scan_prep` calling Claude-Code-shaped
    # collectors for every kind unconditionally. `None` means the kind has no
    # filesystem-shaped posture surface.
    posture_manifest_collectors: PostureCollectors | None = None
    # (mcp_collector, settings_collector) an *installed* agent's posture prep
    # reads through — the installed-branch counterpart to
    # `posture_manifest_collectors` above, so a second installed kind is not
    # scanned with Claude Code's endpoint semantics. `None` means the kind has
    # no filesystem-shaped installed posture surface (e.g. a control-plane
    # kind whose installed state lives behind an API, not on disk).
    installed_posture_collectors: InstalledPostureCollectors | None = None

    def __post_init__(self) -> None:
        """A typo in an allowlist would silently disable an intended rule rather
        than error — fail at kind-construction time, against the same rule ids
        `tools.posture` actually runs."""
        if self.posture_rules is None:
            return
        from tools.posture import KNOWN_RULE_IDS

        unknown = self.posture_rules - KNOWN_RULE_IDS
        if unknown:
            raise ValueError(
                f"kind {self.id!r} allowlists unknown posture rule id(s): {sorted(unknown)}"
            )


def _registry() -> tuple[AgentKind, ...]:
    from tools.agent_kinds import claude_code

    return (claude_code.KIND,)


REGISTRY: tuple[AgentKind, ...] = _registry()


def kind_for(kind_id: str) -> AgentKind:
    for kind in REGISTRY:
        if kind.id == kind_id:
            return kind
    raise KeyError(f"unknown agent kind: {kind_id!r}")


def discover_agents(ctx: DiscoveryContext) -> list[AgentInstance]:
    agents: list[AgentInstance] = []
    seen: set[str] = set()
    for kind in REGISTRY:
        for found in kind.discover(ctx):
            agent = found.validate_against(kind)
            if agent.bom_ref in seen:
                raise ValueError(
                    f"duplicate agent instance key {agent.bom_ref!r}: "
                    f"kind {kind.id!r} discovery returned the same (kind, agent_id) twice"
                )
            seen.add(agent.bom_ref)
            agents.append(agent)
    return agents


def build_agent_graph(
    agent: AgentInstance,
    *,
    include_gitignored: bool = False,
    warnings: list[str] | None = None,
) -> Graph:
    return kind_for(agent.kind_id).compose(
        agent, include_gitignored=include_gitignored, warnings=warnings
    )


def resolve_coverage(baseline: str, *, evidence_gaps: int) -> str:
    """`min(baseline, evidence)` (ADR-0046). Evidence never raises coverage."""
    if baseline not in COVERAGE_LEVELS:
        raise ValueError(f"unknown coverage level: {baseline!r}")
    observed = baseline if evidence_gaps == 0 else "partial"
    return min(baseline, observed, key=COVERAGE_LEVELS.index)


def slugify_agent_id(agent_id: str, *, max_length: int = 64) -> str:
    """A filesystem-safe rendering of an agent id, for output filenames only.

    The instance key keeps the raw value; case, Unicode, separators, and length
    are stricter constraints on a filename than on a key.
    """
    folded = unicodedata.normalize("NFKC", agent_id).strip().casefold()
    slug = _SLUG_UNSAFE.sub("-", folded).strip("-._")
    if len(slug) > max_length:
        digest = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug[: max_length - 9].rstrip('-._')}-{digest}"
    return slug or hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:12]


def output_basenames(agents: Sequence[AgentInstance]) -> dict[str, str]:
    """Map each agent's `bom_ref` to a unique output basename.

    Two distinct agent ids can slug identically (`A/B` and `a-b`), and two files
    cannot share a name, so every member of a colliding group is suffixed with a
    digest of its `bom_ref` — deterministic and independent of discovery order.
    """
    by_basename: dict[str, list[AgentInstance]] = {}
    for agent in agents:
        by_basename.setdefault(agent.output_basename, []).append(agent)
    resolved: dict[str, str] = {}
    for basename, group in by_basename.items():
        if len(group) == 1:
            resolved[group[0].bom_ref] = basename
            continue
        for agent in group:
            digest = hashlib.sha256(agent.bom_ref.encode("utf-8")).hexdigest()[:8]
            resolved[agent.bom_ref] = f"{basename}-{digest}"
    return resolved
