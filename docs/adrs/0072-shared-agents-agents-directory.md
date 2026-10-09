---
id: 0072
title: `.agents/agents` is evidence for every kind that reads it
status: accepted
date: 2026-10-09
supersedes: null
superseded-by: null
---

## Context

ADR-0058 made `.agents/skills/` evidence for every registered kind that reads
it, on the ground that `.agents/` is a cross-tool convention directory rather
than any runtime's own, so its presence cannot identify one agent but must not
identify none.

Devin CLI also reads subagent profiles from `.agents/agents/`, flat
`<name>.md` or `<name>/AGENT.md`. Cursor's spec records that `.agents/` holds
only skills for Cursor, and Codex declares no such root.

## Decision

Apply ADR-0058's rule to the second shared directory: `.agents/agents/` is
evidence for every registered kind that reads it, and composition for the same
kinds. Today that is `devin-cli` alone, so a repository holding only
`.agents/agents/` declares a Devin CLI agent.

## Alternatives considered

- **Evidence for nobody** — rejected for ADR-0058's reason: a repository that
  ships its subagents only in the shared directory would declare no agent while
  holding components an agent loads.
- **Evidence for every registered kind** — rejected. Cursor and Codex do not
  read it, so their BOMs would carry no component for it.

## Consequences

When a second kind starts reading `.agents/agents/`, it adds the pattern to its
own evidence set and a repository holding only that directory emits one BOM
per reader, as `.agents/skills/` already does.

## When to revisit

If `.agents/` gains an owning runtime, or if the count of readers makes
per-kind BOMs of the same shared files unhelpful (ADR-0058's triggers).
