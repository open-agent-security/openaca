---
id: 0058
title: `.agents/skills` is evidence for every kind that reads it
status: accepted
date: 2026-08-28
supersedes: null
superseded-by: null
---

## Context

ADR-0052 made `.agents/skills/` evidence of a **Cursor** agent, and was explicit
that this rested on one contingent fact:

> `.agents/skills/` is the exception and *is* evidence, because Cursor is
> currently the only registered kind that reads it — a claim to revisit if a
> Codex kind lands.

and named the trigger:

> **If a Codex kind registers.** `.agents/skills/` stops being unambiguous
> evidence of a Cursor agent, and the exception carved out above needs
> revisiting.

A Codex kind has registered, and Codex reads it. Its published skills reference
lists repository `.agents/skills` — walked from the working directory up to the
repository root — and `$HOME/.agents/skills` among its discovery locations
([Skills](https://developers.openai.com/codex/skills)).

The Codex spec previously said the opposite, on the evidence that the audited
binary contains no `.agents/skills` string literal. That does not follow: a
program that builds a path from components has no such literal. The same
non-inference produced a second wrong claim in the same spec — that
`[agents.*] config_file` roles do not exist — which review caught twice before
it was checked against the published reference.

## Decision

**`.agents/skills/` is evidence for every registered kind that reads it**, and
composition for every kind that reads it. Today that is Cursor and Codex; a
repository containing only `.agents/skills/` declares both.

This replaces ADR-0052's sole-reader exception. The exception was never about
Cursor specifically — it was about the directory unambiguously identifying
*one* agent, and it stops holding the moment a second kind reads the same path.
The alternative reading, that a shared convention directory is evidence for
nobody, is rejected below.

**ADR-0052 is not superseded as a whole.** Only the sentence carving out
`.agents/skills/` is replaced; its plugin, coverage, identity, and CLI
decisions stand. The frontmatter says `supersedes: null` for the reason
ADR-0057 records: the repository's supersession discipline assumes whole
replacement, and retiring a live decision to amend one clause would be the
worse misstatement.

**`/etc/codex/skills`** — the admin location in the same reference — is **not**
read. It is the same class of administrator-distributed surface as
`managed_config.toml` and is recorded as deferred in the spec rather than
silently skipped.

## Alternatives considered

- **Keep it as Cursor-only evidence** — rejected. It is now demonstrably read
  by two kinds, so the claim it identifies a Cursor agent is simply false, and
  a repository whose only agent surface is `.agents/skills/` would emit a
  Cursor BOM while the Codex agent that also loads those skills goes
  unreported.
- **Make it evidence for no kind** — rejected, though it is the closest call.
  It has the appeal of symmetry with ADR-0052's treatment of `.claude/*` as
  composition-never-evidence. But that rule exists because `.claude/` is
  *another runtime's own directory*, so its presence identifies that runtime,
  not the one reading it. `.agents/` is nobody's own directory — it is a
  cross-tool convention that every participating runtime loads. Treating it as
  evidence for none would make a repository that ships only shared skills
  declare no agent at all, hiding real, loadable components behind a
  technicality.
- **Give `.agents/` its own agent kind** — rejected. It is a directory
  convention, not a runtime: nothing executes it, and there is no config root,
  no version, and no process to attribute components to.

## Consequences

**Enables.** A repository that ships skills only in the shared convention
directory is now attributed to both runtimes that would load them, instead of
one arbitrarily.

**Costs.** A repository containing only `.agents/skills/` now emits two BOMs
where it previously emitted one. That is more output for the same tree, and it
will read as duplication to anyone who has not read this ADR — the skills are
genuinely loaded twice, by two runtimes, and each agent's BOM is its own view.

Cursor's discovery is unchanged; only the claim about exclusivity moves.

**Watch for.** A third kind adopting `.agents/`. The rule generalises by
construction — it is evidence for every kind that reads it — but each addition
multiplies the BOMs a shared-skills repository produces, and at some point that
is worth surfacing differently rather than by repetition.

## When to revisit

- **If `.agents/` gains an owning runtime**, so that its presence identifies
  one agent again rather than a convention several agents share.
- **If the count of kinds reading it makes per-kind BOMs unhelpful** — the
  decision would then be about how to present shared components, not about
  whose evidence they are.
