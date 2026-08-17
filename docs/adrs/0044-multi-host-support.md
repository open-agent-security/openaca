---
id: 0044
title: Host abstraction for multi-host support
status: accepted
date: 2026-08-11
supersedes: null
superseded-by: null
---

## Context

OpenACA has one host wired end-to-end: Claude Code. Config-root discovery,
manifest parsing, posture rules, and BOM identity all assume Claude Code's
shapes — sometimes explicitly (`$CLAUDE_CONFIG_DIR`), sometimes by omission
(parsers hardcoding `runtime_hosts=["claude-code"]`, two posture rules
guessing host from manifest shape instead of being told). This ADR records
the host-agnostic mechanism: the `HostAdapter` abstraction, the
registry-driven repo-mode dispatch pattern, the identity/occurrence rules
every host must follow, and the endpoint-mode architecture. Full design
rationale and the surface-comparison work that motivated it live in
`docs/specs/multi-host-support.md`.

Cursor is the first host built on this mechanism, but the Cursor-specific
decisions — which surfaces it ships, its plugin identity fix, its
Subagent-precedence resolver, its endpoint-mode Plugin limitation, and
every other choice specific to Cursor's own docs and behavior — are
recorded separately in **ADR-0045**, not here. This ADR was originally a
single record covering both the mechanism and Cursor's specific
onboarding; it was split once the Cursor-specific content grew large
enough that a future host's implementer (Codex, Copilot) would otherwise
have to read past a great deal of Cursor-only material to find the
reusable framework. The split happened before this branch was ever
published, so no external reader has cited the original combined text —
splitting carries none of the historical-PR-readability cost the
project's ADR-immutability convention exists to protect.

## Decision

1. Host abstraction is a `HostAdapter` frozen dataclass (config-root
   resolver, host-tagged manifest registry, posture-manifest roots,
   posture-rule allowlist, optional `seed_endpoint` composition function),
   registered in `tools/hosts.py`. **This decision is explicitly scoped
   to registry-driven repo-mode dispatch, not claimed as the durable
   discovery architecture for every future surface.** Repo mode's two
   call sites (`tools/graph_build.py`'s `descend()` dispatch and
   `tools/parsers/__init__.py`'s manifest-pattern registry) are both
   adapter-driven for surfaces that place uniformly (direct child of
   `target`, regardless of host — MCP servers, Skills, and a plugin's own
   top-level manifest all qualify): both `_active_registry` (manifest
   accounting) and `_add_repo_standalone_components`/`_add_project_skills`
   (graph placement) resolve which parser to call by reading
   `HOSTS[host_id].manifest_registry` and matching against it with the
   same `registry_pattern_matches` function. **The expected cost of a
   new host, stated precisely:** registering its `HostAdapter` plus one
   line added to `_MCP_REGISTRY_PATTERNS`/`_SKILL_REGISTRY_PATTERNS` (or
   an equivalent per-surface allowlist) for each genuinely new,
   host-scoped pattern its surfaces use — e.g. a hypothetical
   `.codex/mcp.json` or `**/.codex/skills/*/SKILL.md`. That is the
   realistic path for almost any future host, because each host in
   practice has its own config-directory convention. It is smaller and
   more centralized than a hardcoded dispatch branch across two files,
   but it is **not** zero: reusing an *already*-allowlisted pattern
   *string* verbatim (truly zero `graph_build.py` changes) is not the
   general case. That path only applies when a new host's surface
   happens to share both the identical pattern string *and* the
   identical placement semantics of an existing entry, and even then
   only when it isn't scanned alongside that pattern's existing owner:
   `resolve_host_selection` rejects two distinct, simultaneously
   selected hosts claiming the same pattern string with a clear error —
   closing a real silent-divergence bug (`parse_repo_grouped` would have
   double-counted the path while graph dispatch silently picked
   whichever host came first in `hosts`, making CLI order load-bearing).
   A new host that must coexist with an existing pattern owner needs its
   own host-scoped pattern; sharing the literal string is not a
   supported shortcut. This does **not** generalize to placement-varying
   surfaces (a manifest's *bundled* components, nested contexts):
   `manifest_registry`'s `list[tuple[str, ParserFn]]` shape carries which
   parser to call, not where in the graph its output belongs, and a
   surface is only safe to unify this way when it places uniformly. A
   surface whose bundled components need walking (skills/agents/commands/
   hooks/MCP nested inside a plugin-shaped manifest) is walked
   programmatically by a parameterized root-walker function, not through
   `manifest_registry` at all. No class hierarchy for `HostAdapter`
   itself — the fields that vary per host are data (callables, sets,
   lists), not behavior needing inheritance.
2. Host is provenance (`runtime_hosts` / `openaca:agent_host`), never
   part of `openaca:identity` or any match coordinate — reaffirms
   ADR-0029/0042. **A specific trap to watch for, found and corrected
   once already (see ADR-0045 for the concrete incident):**
   `tools/identity.py`'s `canonical_component_identity` grants a `plugin`
   ref real, cross-BOM `openaca:identity` either from
   `extra["marketplace"]` (verified install-state provenance) or — as an
   apparently-unintentional fallback with no provenance check at all —
   from any `component_identity` string containing 2 or more `/`
   characters. A role-qualified-*looking* identity string built to
   include a host segment (e.g. `plugin/<host>/<name>`) will silently
   trip that fallback and be accepted as real cross-BOM identity backed
   by nothing but a self-declared name — exactly the "local alias
   becomes cross-BOM identity" ADR-0042 forbids, and exactly "host baked
   into identity" this decision forbids. Any new host's parser work must
   trace identity-setting code through `canonical_component_identity`'s
   actual branches before assuming a constructed string is safe, not
   just read the string in isolation.
3. Same-identity components installed under two hosts are two `bom-ref`
   occurrences sharing one `openaca:identity` — not a merged multi-host
   component. The one confirmed exception category: a surface where one
   host can read a file belonging to another host directly, with no
   second file ever created, breaks the "one file per host" assumption
   the rest of this model relies on and needs its own occurrence
   resolver — `manifest_registry`/`registry_pattern_matches` can't
   express it, since that matcher classifies one path in isolation and
   this needs to inspect a sibling path first. ADR-0045 records the one
   confirmed instance (Subagents) and its resolver; that resolver is a
   purpose-built exception scoped narrowly to evidence of real
   cross-host file sharing, not a template to copy elsewhere without
   equally strong evidence.
4. `--host` is repeatable/comma-separated at the CLI, on both
   `openaca scan repo` and `openaca scan endpoint`, with different
   defaults reflecting a real semantic difference: repo mode's default
   is every *registered* host (host selection means "which manifest
   patterns to look for," no machine-state dependency); endpoint mode's
   default is every *detected* host (`detect()` — config root exists on
   disk — genuinely gates which hosts participate, since endpoint mode
   reads real machine state). A machine with only one host's config root
   present behaves identically to a single-host scan; a machine with
   multiple config roots scans all of them with no flag needed.
5. A private `_infer_hosts()`-style helper that guesses host from
   manifest *shape* (e.g. "has an `mcpServers` key") instead of reading
   the host already threaded through `ComponentRef.extra["runtime_hosts"]`
   is a bug wherever it appears, independent of any specific host —
   two posture rules (`insecure_transport.py`, `mcp_auto_approve.py`)
   had this bug and were fixed as part of onboarding the first
   non-Claude host, since a shape-based guess that happened to be safe
   with only one host in existence stops being safe the moment a second
   host can produce the same manifest shape. `skill_capability.py` and
   `mutable_install.py` already read `runtime_hosts` from `ref.extra`
   rather than guessing and needed no change.
6. Endpoint mode's architecture: `HostAdapter.seed_endpoint` is an
   `EndpointSeedFn = Callable[[Graph, Node, Path, Optional[Path],
   SourceNormalizer], None]` (plus a `warnings` keyword) that each host
   owns independently — endpoint mode is fundamentally different per
   host, so no generalized install-model shape is shared across hosts.
   Seed bodies live in `tools/endpoint_seeds/<host>.py` and are bound to
   the frozen adapter through lazy wrappers defined in `tools/hosts.py`
   (deferred import at call time) — seed modules import `graph_build`
   helpers and `graph_build` imports `tools.hosts`, so a static import
   from `hosts.py` would cycle; `hosts.py` types `EndpointSeedFn` from
   `tools.graph` only. `build_graph()`'s endpoint branch is a loop over
   an explicit, CLI-resolved `{host_id: config_root}` map (default:
   every *detected* host's default root; an explicit config-dir override
   supplies a single selected host's root without a `detect()` check):
   for each host in the map, call its `seed_endpoint(graph, root,
   host_config_root, project_root, normalize, warnings=warnings)` —
   same shared `Graph`, same single target `Node`, each host's call
   contributing its own children onto it — followed by one cross-host
   Subagent pass run by the branch itself (a shared-file Subagent
   occurrence can span hosts, so no single host's seed can own it). The
   graph's root-sensitive stages are multi-root with per-host node-key
   labels (`endpoint/` remains Claude Code's label for key
   stability; other hosts get `endpoint-<host_id>/`), manifest-name
   indexes are kept per root — each MCP node resolves against the index
   of the root that seeded it, project entries still taking precedence,
   with no cross-host name fallback — and launch-dependency resolution
   uses the root that seeded each node. A host with `seed_endpoint=None`
   simply contributes nothing in endpoint mode until its adapter is
   filled in; repo-mode-only support is a valid intermediate state for a
   new host.
7. Any remote consumer's per-host ingest change (persisting
   `openaca:agent_host`/`openaca:runtime_hosts`, excluded from any
   cross-BOM join key) is a coordinated, separate change in that
   consumer's own repository; this design has no aggregated-view payoff
   until one lands, independent of which hosts are registered.

## Alternatives considered

- **Single install-model shape shared across hosts**: force every host's
  endpoint adapter to implement the same settings-merge-plus-lockfile
  machinery Claude Code has. Rejected — that shape is Claude-specific by
  construction (a settings-layer merge plus a JSON lockfile), not a
  general contract every host must satisfy; a host with no lockfile at
  all, or a completely different install-state mechanism, must be able
  to set `seed_endpoint` to something that doesn't replicate it.
- **Class hierarchy (`BaseHostAdapter` with per-host subclasses)**:
  rejected in favor of a plain frozen dataclass + registry — the fields
  that vary per host are data (callables, sets, lists), not behavior
  needing inheritance, and a dataclass keeps each host's definition a
  flat, readable declaration.
- **`detect()` uniform across repo and endpoint mode**: an earlier design
  draft treated "config root exists on disk" as the selection mechanism
  for both modes. Rejected for repo mode specifically — a repo committing
  a host-specific manifest must scan correctly regardless of whether the
  scanning machine happens to have that host's config root installed, so
  repo-mode default host selection cannot depend on local machine state
  the way endpoint mode's does.
- **Running `run_posture_rules` once per host**: rejected — ref-keyed
  rules (`mutable_install`, `skill_capability`) have no manifest filter,
  so a per-host dispatcher call would double- or triple-count their
  findings. Manifest collection is unioned across hosts up front instead;
  the dispatcher still runs once per scan.

## Consequences

**Enables:** A new host's MCP/Skills-shaped coverage — in *both* manifest
accounting and the actual composition graph that produces BOM/matching/
findings — follows from registering its `HostAdapter` plus one centralized
allowlist line per genuinely new pattern, not a hardcoded dispatch branch
scattered across files. `resolve_host_selection` closes the one way
manifest accounting and graph placement could still disagree (duplicate
host ID or pattern-string collision), failing identically at both call
sites rather than one silently over-counting.

**Costs:** The registry-driven unification doesn't generalize past
surfaces that place uniformly — a surface whose graph placement varies by
context (bundled components, nested dependency manifests) can't reuse
`manifest_registry` as-is, since its `(pattern, ParserFn)` tuple carries
no placement information. Extending past a directly-placed surface means
designing that placement information in, not just making more of
`descend()` iterate the registry. A surface where two hosts can share
literal file ownership needs its own purpose-built resolver, not an
extension of `resolve_host_selection` — that guard only recognizes exact
pattern-string collisions between two selected hosts' own registry
entries.

**Watch:** If a future surface is added to `manifest_registry` under the
assumption that "just walk the registry" is now a solved pattern, and its
placement turns out not to be uniform across hosts, that's the signal
this needs real design work (a placement-aware descriptor), not another
allowlist entry copied from an existing surface's shape.

## When to revisit

- When a future surface needs registry-driven graph dispatch beyond
  directly-placed manifests: `manifest_registry`'s current shape,
  `list[tuple[str, ParserFn]]`, is sufficient only because every surface
  using it places as a direct child of `target` regardless of host —
  nothing about the tuple shape carries placement information. Don't
  assume "add it to an allowlist" generalizes mechanically — design the
  placement-aware descriptor explicitly (see this ADR's Watch note
  above) before extending the pattern-matching dispatch functions to a
  surface whose placement isn't a flat direct-child add.
- If a future host needs to share ownership of one physical manifest or
  skill file with an existing host — one occurrence with multiple
  `runtime_hosts`, not two competing occurrences — `resolve_host_selection`
  is not that mechanism and shouldn't be extended to fake it. It only
  recognizes *exact pattern-string* collisions between two selected
  hosts' own `manifest_registry` entries; it says nothing about two
  *different* pattern strings that happen to match the same physical
  path. A genuine shared-occurrence feature needs its own explicit
  design, following the precedent (not the code) of ADR-0045's Subagents
  resolver.
- **Before registering a third host, make `tools/host_paths.owning_host`
  registry-derived.** `owning_host` is a hardcoded two-branch function
  (exact `(".cursor", "mcp.json")` tail → `"cursor"`, everything else →
  `"claude-code"`) — unlike `_active_registry`, `_mcp_parser_for_path`,
  and `_skill_parser_for_path`, it never consults `HOSTS`. A third host
  that does exactly what this ADR's Decision #1 says is the expected
  cost — register a `HostAdapter`, add one line to
  `_MCP_REGISTRY_PATTERNS` for its own host-scoped pattern (e.g.
  `.newhost/mcp.json`) — is silently misattributed to `claude-code` by
  `owning_host`, which reproduces the exact registry/graph divergence
  bug this ADR's whole design exists to prevent: the file is
  double-counted by `parse_repo_grouped` (matched by both the new
  host's pattern and Claude's bare-basename catch-all, since
  `owning_host` never returns the new host's ID to exclude it) while
  the graph attributes it to the wrong host. `resolve_host_selection`'s
  collision guard does not catch this, because the two patterns
  involved are different strings, not the same one. Fix before or when
  a third host lands: derive `owning_host` from `HOSTS`, scanning each
  adapter's host-scoped (`/`-containing) `manifest_registry` patterns,
  via the same deferred-import approach `_active_registry` already
  uses to avoid the `tools.hosts`/`tools.parsers` import cycle — and add
  a drift-guard test asserting every registered host's patterns are
  recognized by both the registry's `owning_host` classification and
  every surface's own pattern allowlist, so this class of gap fails a
  test instead of requiring a live CLI run to surface it.
