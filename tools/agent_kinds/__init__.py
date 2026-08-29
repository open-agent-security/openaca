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
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from tools.bom import AGENT_ROOT_PREFIX
from tools.capability import COVERAGE_LEVELS
from tools.component_ref import ComponentRef
from tools.graph import Graph
from tools.parsers import ManifestPattern

if TYPE_CHECKING:
    # Annotation-only (this module has `from __future__ import annotations`),
    # so `agent_kinds` gains no runtime import edge. ADR-0044's one-way rule
    # constrains `graph_build`, not this module, but keeping the edge to
    # type-check time costs nothing and leaves import order untouched.
    from tools.repo_surface import RepoSurface

Cardinality = Literal["singleton", "many_per_place"]
CompositionSource = Literal["installed", "declared"]
# A kind's declared manifest surface (spec: "It declares discovery,
# composition, its manifest patterns ..."). Same shape as
# `tools.parsers.REGISTRY` — a `ManifestPattern` per entry — so a kind can
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
    # True only when `config_root` came from an explicit `--config-dir` flag
    # rather than a kind's own env-var/default resolution. `None`-refusal
    # kinds (ADR-0054) can otherwise still read a surface that a root
    # override does not relocate (ADR-0059 for Codex's `$HOME/.agents/skills`);
    # a kind's `compose` consults this to skip that surface instead of
    # silently stitching two homes together.
    config_root_overridden: bool = False

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
    # Limits discovery to one kind. `None` (the default) discovers every
    # registered kind — the single-kind-machine behaviour is unaffected either
    # way since there is only one kind to find.
    kind_id: str | None = None


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
    manifest_patterns: tuple[ManifestPattern, ...] = ()
    # The repo-mode surface descriptor those patterns belong to (ADR-0053).
    # Carrying it here is what makes plugin-ownership exclusion a property of
    # the KIND rather than of each consumer that walks a tree: registry
    # accounting and evidence detection both reach it through this field, so
    # a third kind inherits the rule by declaring a surface instead of by
    # remembering to call a predicate. `None` for kinds with no filesystem
    # repo surface at all.
    repo_surface: RepoSurface | None = None
    # (mcp_collector, settings_collector) a *declared* agent's posture prep
    # reads through, instead of `_agent_scan_prep` calling Claude-Code-shaped
    # collectors for every kind unconditionally. `None` means the kind has no
    # filesystem-shaped posture surface.
    # Why a kind names a root can be *fully* specified by `--config-dir`
    # (ADR-0054). A kind qualifies only when no part of its composition is
    # derived from the home directory once a root is named — for Claude Code
    # home is a default, for Cursor home is an ingredient, and a kind that
    # reads anything home-derived after a root is named does not qualify
    # however small that surface is. `None` means the kind accepts an override;
    # a string is the reason it refuses, surfaced verbatim by the CLI.
    root_override_refusal: str | None = None
    posture_manifest_collectors: PostureCollectors | None = None
    # (mcp_collector, settings_collector) an *installed* agent's posture prep
    # reads through — the installed-branch counterpart to
    # `posture_manifest_collectors` above, so a second installed kind is not
    # scanned with Claude Code's endpoint semantics. `None` means the kind has
    # no filesystem-shaped installed posture surface (e.g. a control-plane
    # kind whose installed state lives behind an API, not on disk).
    installed_posture_collectors: InstalledPostureCollectors | None = None
    # Posture surfaces that declare no components, keyed by the rule id that
    # consumes them. The `(mcp, settings)` pair above has no room for a third
    # or fourth channel, and overloading either slot would make one rule's
    # input depend on another's manifest shape. `None` means the kind has no
    # such surfaces — every existing kind is unaffected.
    extra_installed_posture_collectors: Mapping[str, InstalledPostureCollector] | None = None

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


def matches_evidence(rel: str, patterns: tuple[str, ...]) -> bool:
    """Does `rel` match one of a kind's declared-evidence glob patterns?

    Shared by `claude_code` and `cursor`: a `*/`-prefixed pattern is meant to
    match at any depth, but `fnmatch` only matches one leading segment for
    it, so each such pattern is also tried against every suffix of `rel`.
    """
    for pattern in patterns:
        if fnmatch(rel, pattern):
            return True
        if pattern.startswith("*/") and fnmatch(rel, f"*/{pattern}"):
            return True
    return False


def _registry() -> tuple[AgentKind, ...]:
    from tools.agent_kinds import claude_code, codex, cursor

    return (claude_code.KIND, cursor.KIND, codex.KIND)


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
        if ctx.kind_id is not None and kind.id != ctx.kind_id:
            continue
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
