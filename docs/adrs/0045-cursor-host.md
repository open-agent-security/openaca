---
id: 0045
title: Cursor as the first new host
status: accepted
date: 2026-08-11
supersedes: null
superseded-by: null
---

## Context

ADR-0044 records the host-agnostic `HostAdapter` mechanism: the
registry-driven repo-mode dispatch pattern, the identity/occurrence
rules every host must follow, and the endpoint-mode architecture. This
ADR records the decisions specific to Cursor — the first host built on
that mechanism — surface scope, sequencing, the concrete identity bug
found and fixed while onboarding it, its Subagent-precedence resolver,
and its endpoint-mode Plugin limitation. Full design rationale, the
Claude-vs-Cursor surface audit, and the terminology grounding live in
`docs/specs/multi-host-support.md`; this ADR records the decisions, not
the audit that produced them.

This ADR was originally the Cursor-specific half of ADR-0044, split out
once that combined record grew large enough that a future host's
implementer (Codex, Copilot) would have to read past a great deal of
Cursor-only material to find the reusable framework. Two increments are
recorded together in this single ADR rather than split further: repo-mode
MCP servers and Skills (accepted and shipped first), and Plugins (both
the native Cursor Plugins format and the Agent Plugins open standard),
Subagents, Commands, and endpoint mode for every surface above (this
revision). The branch carrying both increments — and the original
combined ADR-0044/0045 record — was never published, so no external
reader has cited any of this text; splitting and amending it in place
carries none of the historical-PR-readability cost the project's
ADR-immutability convention exists to protect.

## Decision

1. Cursor ships MCP servers, Skills, Plugins (both native Cursor Plugins
   and the Agent Plugins open standard), Subagents, and Commands — in
   **both** repo mode and endpoint mode. Hooks (standalone,
   `.cursor/hooks.json`), Rules, `AGENTS.md`, and Extensions remain
   deferred, not declined — each is a real, popular surface with a
   named unlock condition, not excluded for lacking a Claude Code
   equivalent. Standalone Hooks staying deferred is not a new gap
   relative to Claude: Claude's own standalone hooks aren't a top-level
   repo-mode registry entry today either (only reachable via plugin
   bundling or `settings.json.hooks` in endpoint mode). Plugin-*bundled*
   hooks are in scope as part of Plugins support below.
2. **Cursor's plugin identity is the unqualified `plugin/{name}` string
   Claude's own repo-mode plugins already use — not a Cursor- or
   format-specific namespace.** This corrects a real mistake found and
   fixed during this ADR's own design, the concrete instance of the trap
   ADR-0044 Decision #2 warns about generically. An earlier draft
   proposed `plugin/cursor/<name>` ("role-qualified per ADR-0042"),
   reasoning by analogy to `mcp-server/npm/<pkg>`-style role
   qualification. Tracing the actual identity computation in
   `tools/identity.py`'s `canonical_component_identity` showed this was
   wrong: a `plugin` ref gets a real, cross-BOM `openaca:identity`
   either from `extra["marketplace"]` (verified install-state
   provenance) or — as an apparently-unintentional fallback — from any
   `component_identity` string with 2 or more `/` characters, with no
   provenance check at all. `plugin/cursor/<name>` has exactly 2, so it
   would have been accepted as real cross-BOM identity backed by
   nothing but a self-declared `name` field — exactly the "local alias
   becomes cross-BOM identity" ADR-0042 forbids, and exactly "host baked
   into identity" ADR-0044's Decision #2 forbids. **Corrected:** both
   Cursor plugin formats use `plugin/{name}`. With no marketplace info
   available for either host in repo mode, `canonical_component_identity`
   correctly returns `None` for both — occurrence-local, differentiated
   only by `runtime_hosts`/`bom-ref`, consistent with every other
   surface in this design. No code change in `tools/identity.py` was
   needed; the fix is entirely in what string the plugin parser
   constructs.
3. **Agent Plugins' bundle walking is narrower than Cursor Plugins'.**
   The open standard's v1 spec portably standardizes only `skills/` and
   `mcp.json` across compliant clients — verified directly against the
   spec repository (`agentplugins/agent-plugins-spec`), not assumed;
   also corrects an earlier draft's claim that `name`/`description`/
   `version`/`author` are all required (only `$schema` and `name` are).
   Commands, agents, hooks, and rules are explicitly out of the v1
   portable contract, left to client-private
   `extensions.<reverse-domain>` namespacing this design does not parse
   — the spec's own words: they "remain too client-specific for a
   stable portable contract... until their formats converge." Bundled
   Rules and `variables` are not walked for either plugin format — Rules
   has no identity model yet (same gap as the top-level Rules surface),
   `variables` is plugin configuration, not a component.
4. **Cursor's compatibility read of `.claude/agents/*.md` is confirmed
   unconditional, with an explicit precedence rule — verified against
   Cursor's own subagents documentation, not assumed.** A same-relative-
   path override under `.cursor/agents/` wins, and Cursor never reads
   Claude's copy in that case. This is the concrete instance of the
   occurrence-model exception ADR-0044 Decision #3 names generically.
   The resolver: for every `.claude/agents/<rel>.md`, if no
   same-relative-path `.cursor/agents/<rel>.md` exists, it is **one**
   occurrence with `runtime_hosts: ["claude-code", "cursor"]`; if one
   does exist, both files are separate single-host occurrences (Cursor's
   copy is `["cursor"]`, Claude's is `["claude-code"]` — Cursor's own
   file wins the read, so Claude's is not double-counted as also being
   Cursor's). "Same subagent" is matched by relative file path, not
   frontmatter `name:` — the more literal, verifiable reading of
   Cursor's own "same name" wording, and the one an author creating an
   override file would actually produce. Subagents gets a dedicated
   resolver on both the manifest-accounting and graph-placement sides,
   run independently at both global and project scope in endpoint mode
   — this can't be expressed through `manifest_registry`/
   `registry_pattern_matches`, which classifies one path in isolation.
   This exception stays scoped to Subagents alone — no other Cursor
   surface has a confirmed cross-host file-sharing behavior.
5. Commands is scoped to project-level only (`.cursor/commands/*.md`);
   no personal/global path is documented for Cursor commands, and no
   cross-host compatibility read is documented either (unlike
   Subagents) — guessing either would repeat the same
   capability-guessing this design avoids elsewhere.
6. `mcp_auto_approve` does not apply to Cursor at all — verified against
   Cursor's own MCP documentation, its approval model is Run-Modes/UI
   state with no manifest-level `autoApprove` field, so flagging one on
   a Cursor-owned manifest would assert an active posture Cursor's
   config surface doesn't support. This is the concrete Cursor evidence
   behind the general `_infer_hosts()` bug fix recorded in ADR-0044
   Decision #5 — `insecure_transport.py`'s fix applies uniformly once
   host is read correctly, but `mcp_auto_approve.py`'s fix additionally
   needed a per-manifest skip for Cursor specifically, since the field
   it keys on has no Cursor equivalent at all. No other posture rule
   needs a Cursor-specific change: `skill_capability.py` gates on
   `component_type == "skill"` explicitly, so Commands/Subagents/Plugins
   refs never reach it; `mutable_install.py`'s plugin branch only fires
   when `"gitCommitSha" in ref.extra`, a marker only Claude's
   `installed_plugins.json` path sets, so Cursor's presence-only,
   lockfile-less plugin refs correctly never trigger it either —
   verified by reading `_mutable_install_source_for` directly.
7. **Cursor's endpoint-mode Plugin support seeds both dev-linked and
   marketplace-cached plugins, presence-only — never an
   enabled/disabled state.** The evidence, verified rather than
   assumed: Cursor's plugin enable state lives in an undocumented VS
   Code-inherited `state.vscdb` SQLite database (community-documented —
   cursor/plugins#136 — with no maintainer confirmation and known
   misleading key semantics: `installedIds` holds enabled-only), and
   cache presence explicitly does NOT imply enabled — disabling a
   plugin retains its cache. Marketplace-installed plugins land under
   `~/.cursor/plugins/cache/<marketplace>/<name>/<commit-sha>/` with a
   `.cache-complete` sentinel written beside the bundle when the
   download finished — a layout observed directly on a real endpoint
   and corroborated by cursor/plugins#136, still absent from Cursor's
   official reference (which also contradicts observed reality on
   manifests: it requires `.cursor-plugin/plugin.json`, yet real
   marketplace plugins ship without one). An earlier increment of this
   decision scoped seeding to dev-linked only because the cache layout
   was then unconfirmed; a real endpoint with five marketplace plugins
   producing zero components fired that scoping's revisit trigger, and
   the observed+corroborated layout resolved it. The design, in five
   points:

   1. **Discovery**: `<config_root>/plugins/local/*/` (dev-linked) and
      every `plugins/cache/<marketplace>/<name>/<version>/` directory
      carrying a `.cache-complete` sentinel; incomplete downloads are
      skipped. Multiple cached versions of one plugin seed as multiple
      occurrences — each is a real bundle on disk.
   2. **No enabled-state, ever**: no `enabled`/`active` key is set —
      the property is absent, not `false`. Cache presence means
      "downloaded artifact present," the honest supply-chain claim and
      the only one the evidence supports. Parsing `state.vscdb` to do
      better would be exactly the capability-guessing this project's
      discipline forbids (the same reasoning behind Decision #6's
      `mcp_auto_approve` skip). This is not the claim Claude's
      endpoint-mode plugin refs make (`enabledPlugins ∩
      installed_plugins.json`, a real signal); the two must not be
      conflated by downstream consumers or renderers.
   3. **Identity stays unqualified `plugin/{name}`.** The cache path's
      `<marketplace>` segment is an observed directory name, not
      verified install-state (ADR-0042: local names never become
      cross-BOM identity), so `extra["marketplace"]` is never set from
      it — that key would mint qualified cross-BOM identity via
      `canonical_component_identity`. The segment is recorded as
      non-identity provenance under `extra["cursor_marketplace_dir"]`,
      which the identity code never reads.
   4. **Manifest handling matches dev-linked**: native
      `.cursor-plugin/plugin.json` wins per directory; an Agent Plugins
      root `plugin.json` realizes through the closed portable surface
      when no native manifest exists. A manifest-less cached bundle
      (observed in the official marketplace) seeds a synthesized
      presence-only self ref named from its `<name>` directory segment,
      marked `extra["manifest"] = "absent"`, with its bundled
      `skills/`/`commands/` walked — skipping it silently would
      reproduce the exact invisibility this decision exists to fix. Its
      unqualified identity is nulled by `canonical_component_identity`
      (no verified marketplace), so no cross-BOM identity is minted
      from a directory name.
   5. **Posture parity with Claude**: Cursor's endpoint posture
      collector derives bundled `mcp.json` manifests from the seeded
      plugin refs (cached and dev-linked), the same way Claude's
      collector derives plugin install roots from its refs, so bundled
      MCP posture rules apply to marketplace installs.

   Every other Cursor surface in endpoint mode (MCP, Skills, Subagents,
   Commands) has no such limitation — they're direct file reads with no
   lockfile-backed install-state to resolve, per ADR-0044 Decision #6's
   "no shared install-model shape" reasoning.

## Alternatives considered

- **Scope Cursor to "MCP servers only" for repo mode**: an early pass at
  this design's surface audit did exactly this, based on Cursor's MCP
  and Rules docs alone. Rejected once the audit was redone against
  Cursor's current docs — Cursor ships a Plugins/Marketplace system,
  Hooks, and Agent Skills (the same agentskills.io spec Claude Code
  uses) that a stale audit missed.
- **Only cover Cursor Plugins (native format), defer Agent Plugins**:
  reasoning "since Anthropic isn't a listed Agent Plugins adopter,
  parsing it doesn't unlock any Claude-side reuse today." Rejected —
  this repeats the same Claude-parity logic already rejected for
  Rules/`AGENTS.md`/Extensions; nothing blocks parsing both, and
  detection between the two formats is unambiguous (manifest location
  plus `$schema`), never guessed.
- **Match Subagent overrides by frontmatter `name:` instead of relative
  file path**: rejected — Cursor's docs say "same name" without
  specifying which, and name-based matching would require indexing
  every subagent's frontmatter before resolving any single file's
  occurrence count, a materially bigger and less literal mechanism for
  a distinction the docs don't actually draw.
- **Guess a personal/global Commands path for Cursor** (e.g.
  `~/.cursor/commands/`, following the pattern every other Cursor
  surface uses): rejected — no path is documented anywhere found;
  inferring one from a pattern is exactly the capability-guessing this
  design avoids elsewhere, and a wrong guess would silently under- or
  over-report.
- **Parse Cursor's `state.vscdb` to recover plugin enable state**:
  rejected — undocumented internal VS Code state-database schema, not a
  public contract (community-documented only, no maintainer
  confirmation, misleading key semantics); parsing it would be fragile
  across Cursor versions and is the same class of capability-guessing
  already rejected for `mcp_auto_approve` on Cursor.
- **Treat cache presence as enabled**: rejected — cursor/plugins#136
  establishes that disabling retains the cache, so this would assert
  false enabled-state, worse than no claim in a security inventory.
- **Keep endpoint Plugin seeding dev-linked-only until Cursor documents
  the cache layout**: rejected once the layout was corroborated by two
  independent observations (a real endpoint, community documentation) —
  the dominant real-world install path stayed invisible (observed 5/5
  marketplace plugins missed), and the residual risk of a silent layout
  change fails safe: fewer components, never wrong ones.
- **Qualify identity with the cache path's marketplace directory
  segment**: rejected — ADR-0042/ADR-0044 Decision #2: only verified
  install-state may qualify cross-BOM identity; a directory name is not
  that. Claude's marketplace qualification comes from a host-written
  lockfile; Cursor has no equivalent.
- **Skip manifest-less cached bundles**: rejected — reproduces the
  invisibility bug for real marketplace plugins that ship only
  skills/commands; the synthesized ref is clearly marked
  (`extra["manifest"] = "absent"`) and identity-inert.

## Consequences

**Enables:** Cursor repos and endpoints become scannable for MCP
servers, Skills, Plugins (both formats), Subagents, and Commands, with
correct host provenance and correct cross-host identity on every ref —
in both repo and endpoint mode, closing the gap the first increment of
this ADR (MCP/Skills, repo mode only) explicitly left open.

**Costs:** Subagents' precedence-aware resolver is a genuinely new
mechanism, run twice in endpoint mode (global and project scope) — more
surface area for a bug than any other Cursor surface, precisely because
it's the one place two hosts can read the same physical file. Cursor's
endpoint-mode Plugin support carries no enabled-state signal — a real
gap until Cursor's install-state storage becomes independently
readable, not a temporary implementation shortfall — so a
disabled-but-cached plugin appears in inventory (intended: the code is
on disk; consumers must read the absent enabled property as the
signal, never presence as "active"). Marketplace-cache discovery
depends on an undocumented layout: a Cursor release that restructures
the cache silently reduces coverage until discovery is updated —
failing safe (under-reporting, never misattribution). Multiple cached
versions of one plugin inflate component counts relative to Cursor's
own "installed" view. Commands' project-only scope means a
hypothetical personal-level Cursor command is invisible to this design
until a path is confirmed. Agent Plugins' narrower bundle-walking means
a plugin author who populates Cursor-Plugins-shaped fields
(`commands`/`agents`/`hooks`) inside an Agent-Plugins-formatted manifest
gets those fields silently ignored, not an error — matching the
portable-contract boundary rather than guessing at non-standard content.

**Watch:** If Cursor documents the cache layout or the `state.vscdb`
schema, revisit Decision #7 — this design reads the observed layout but
deliberately did not reverse engineer the enable-state database.

## When to revisit

- When Cursor's `state.vscdb` enable-state schema is documented (or a
  maintainer confirms it) — add enabled-state to cached-plugin refs and
  revisit Decision #7's presence-only rule.
- When Cursor officially documents the plugins-cache layout — replace
  Decision #7's observed-layout citation and align discovery with any
  differences.
- If the marketplace ever writes a host-owned lockfile equivalent to
  Claude's `installed_plugins.json` — revisit identity qualification
  (`extra["marketplace"]`) from that verified source.
- If a Cursor personal/global Commands path is ever confirmed — revisit
  Decision #5's project-only scoping for Commands.
- If a second host (Codex) adopts the Agent Plugins open standard,
  revisit whether its parser should move to host-agnostic treatment like
  Skills (ADR-0018/0031) rather than staying scoped to the Cursor host
  module.
- If Cursor's own docs ever specify global-vs-project precedence
  semantics for MCP/Skills/Commands (unlike Subagents, which has a
  confirmed rule) — currently defaults to simple union/presence.
