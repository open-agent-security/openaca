---
id: 0055
title: Register Codex as the third agent kind, with readable enable state
status: accepted
date: 2026-08-27
supersedes: null
superseded-by: null
---

## Context

ADR-0044 defined a *kind* as what reads a composition; ADR-0052 registered a
second. Two kinds shipped, and they sit at opposite ends of the design space:
Claude Code has a relocatable root, a marketplace registry, and readable enable
state; Cursor has none of those, reads three other runtimes' trees, and is
presence-only because its plugin state is a server call.

Codex is the third, and the audit result decides most of this decision:
**Codex is Claude Code-shaped.** That was verified against the shipped binary
(`0.147.0-aarch64-apple-darwin`), not inferred from documentation or from the
fact that both are CLI coding agents. Full surface detail:
[Codex Agent Kind](../specs/codex-agent-kind.md).

The evidence that settles the shape:

- Plugin enable state is a **file**: `[plugins."<name>@<marketplace>"] enabled`
  in `config.toml`, keyed byte-identically to Claude Code's `enabledPlugins`.
- The plugin cache layout is Claude Code's:
  `plugins/cache/<marketplace>/<name>/<version>/`, with a marketplace registry
  (`[marketplaces.*]`) carrying `source_type`, `source`, and `last_revision`.
- Hook events are **PascalCase and share Claude Code's names**, unlike Cursor's
  camelCase.
- `$CODEX_HOME` relocates the whole root, as `$CLAUDE_CONFIG_DIR` does.

And the evidence that separates it from Cursor: `.claude/skills`,
`.claude/agents`, `.agents/skills`, and `installed_plugins.json` each have
**zero** references in the audited binary. Codex is not a cross-reader.

## Decision

**Codex registers as a third agent kind**, `codex`, singleton, with both
composition sources.

In scope: MCP servers (`[mcp_servers.*]` in `config.toml`), skills, subagents,
plugins in both manifest formats, plugin-bundled hooks, and project
`.codex/hooks.json`. As **posture** surfaces, declaring no components:
`rules/*.rules` and `[projects.*] trust_level`.

**Plugins and MCP servers carry real `enabled` state.** This is the decision
that most visibly differs from ADR-0052, and it is the same principle applied to
different evidence. ADR-0052 rejected an `enabled` property because Cursor's
state is a server call, so the value would have been *fabricated* — and a
fabricated `false` reads as a verified negative. Codex writes the value to a
file we parse. Withholding a fact we can read would be the mirror error.

**Everything installed is inventoried, enabled or not.** A disabled plugin is
still installed, still on disk, and one config edit from active. The `enabled`
property records which; it does not gate membership.

**Subagents are user-scope TOML.** `~/.codex/agents/*.toml` with `name`,
`description`, `developer_instructions`. `.codex/agents` has zero references, so
a project cannot declare a Codex subagent. This needs a new parser — the one
genuinely new parsing surface this kind adds.

**There is no commands surface.** Both other kinds have one; Codex does not.
Shipping a commands parser here would report components no agent has.

**`AGENTS.md` is out**, on Claude Code's rule rather than a new one. Codex reads
it heavily (55 references, more than any in-scope surface), and it is still not
configuration: it names nothing, versions nothing, and resolves to no artifact.
No registry pattern, parser, or evidence rule in this repository touches an
instruction file for any kind today, and Claude Code ignores its own `CLAUDE.md`
for exactly this reason. Following that keeps the treatment uniform and requires
no taxonomy ADR.

**`skills/.system/` is excluded structurally**, by its
`.codex-system-skills.marker` file rather than by a list of names.

**ADR-0052's revisit trigger does not fire.** That ADR made `.agents/skills/`
evidence of a Cursor agent because Cursor was the only registered kind reading
it, and flagged the claim to revisit if a Codex kind landed. One has, and Codex
does **not** read `.agents/skills`. The Cursor claim survives unchanged.

## Alternatives considered

- **Model Codex on Cursor because both are "the newer kind"** — rejected on
  evidence. The audit shows the opposite: Codex shares Claude Code's plugin
  model, hook vocabulary, and root semantics, and shares none of Cursor's
  defining traits. Following the Cursor template would have forked what should
  be parameterized and would have made plugins presence-only for no reason.
- **Presence-only plugins, for consistency with ADR-0052** — rejected. The
  consistency would be cosmetic. ADR-0052's reasoning is about *evidence*, not
  about a house style, and applying its conclusion where its premise is false
  discards a readable fact.
- **Inventory only enabled plugins** — rejected. It matches what the runtime
  loads but hides installed-but-off artifacts, which is precisely the population
  a supply-chain tool should surface.
- **Ship `AGENTS.md` as a Codex component** — rejected. It would contradict
  ADR-0052 without superseding it, and leave Claude Code ignoring `CLAUDE.md`
  while Codex reports `AGENTS.md` — an inconsistency across kinds for the same
  class of file.
- **Build a `.codex/agents` project surface** — rejected because no code path
  reads it. Cursor's documentation claims this root too, and neither runtime
  references it; the docs error is upstream of both.
- **Copy Cursor's hardcoded six-name denylist to exclude built-in skills** —
  rejected. Those six names are exactly the contents of `skills/.system/`, and
  Codex marks that directory with a marker file. Keying on the marker is stable
  where a name list drifts.
- **Treat `[projects.*] trust_level` as composition** — rejected because it
  declares no components. It says which directories may run unattended, which is
  posture, and modeling it as composition would inventory paths as artifacts.

## Consequences

**Enables.** The first kind whose installed coverage is *upgradeable*: two of its
five gaps (`managed_config.toml`, profile layering) close by parsing, where
Cursor's dominant gap cannot.

It is also the first kind whose baseline **splits** — `complete` at `declared`,
`partial` at `installed` — which is the discipline stated rather than a
convenience: a baseline is argued from a named gap at *that* source, never
inherited from the other and never set conservatively because a kind is new. A
`partial` every kind carries by reflex stops distinguishing anything, and one
that nothing blocks is a disclaimer. Every surface a Codex repo declares parses
in full, so `declared` is `complete`; Cursor is `partial` there only because its
extensibility flag is not file-readable, which Codex has no equivalent of. It is also the first kind to exercise a TOML
configuration surface and a non-markdown subagent format.

**Costs.** A new TOML config parser and a new TOML subagent parser, neither
reusable from the two existing kinds. `mcp_auto_approve` must be generalized —
it currently hardcodes `active_in=["cursor"]` — which is a change to a shipped
Cursor path and needs its own regression gate.

Emitting `enabled` means two kinds now answer the same question differently:
Cursor omits the property, Codex sets it. That asymmetry is correct and will
look like an inconsistency to anyone who has not read both ADRs.

**Watch for.** Codex ships fast and the plugin cache layout is undocumented. If
it changes, plugin discovery returns zero — failing safe, but silently. A canary
test asserting a non-empty plugin set on a fixture endpoint is the cheap guard.

A local marketplace whose `source` points into `~/.claude/plugins/local/` means
Codex and Claude Code can realize the same bundle from the same directory. That
is the first genuine same-artifact-two-kinds case in the corpus and this ADR
does not settle it; see the spec's Identity section.

## When to revisit

- **If Codex gains a commands surface**, or project-scoped subagents. Both are
  absent today by verified reference count, not by omission.
- **When `managed_config.toml` is audited.** It is an admin-distributed layer
  that changes what an MDM-managed endpoint composes, and closing it raises
  installed coverage.
- **If a fourth kind reads `.agents/skills`.** The ADR-0052 exception survives
  Codex specifically because Codex does not read it; that is a per-kind fact, not
  a general one.
- **If instruction files gain a content-evidence component type.** `AGENTS.md`
  belongs to it, and it would have to land for every kind at once.
