---
id: 0074
title: A root Agent Plugins manifest is evidence for every kind that reads it
status: accepted
date: 2026-10-09
supersedes: null
superseded-by: null
---

## Context

ADR-0058 made `.agents/skills/` evidence for every registered kind that reads
it: a cross-tool convention is nobody's own file, so its presence cannot
identify one agent but must not identify none. ADR-0072 applied the rule to
`.agents/agents/`.

A root `plugin.json` under the Agent Plugins standard is the same kind of
thing. Cursor reads it, and it is Cursor evidence, at the one schema version
Cursor supports. Devin CLI reads it too, as the third of its plugin manifest
candidates, and reads any Agent Plugins version: "An unrecognized `$schema`
version is warned about and the plugin still loads best-effort" (Devin's
bundled `plugins/overview.mdx`), "with Agent Plugins 1.0.0 rules" (the
3000.11.3 binary). Devin's evidence set did not include it, so a repository
whose only plugin was a manifest of a version Cursor does not support declared
no agent at all, and its skills and MCP servers reached no BOM.

## Decision

Apply ADR-0058's rule to the portable plugin manifest: a root Agent Plugins
`plugin.json` is evidence for every registered kind that reads it, and
composition for the same kinds. Each kind decides by its own format
resolution, the one composition uses, so evidence cannot drift from it:

- Cursor, at the schema versions it supports (unchanged).
- Devin CLI, at any Agent Plugins schema version, and only where the manifest
  is the one Devin reads at its root: a `.devin-plugin/plugin.json` or
  `.claude-plugin/plugin.json` beside it takes precedence.

A repository holding only a 1.0.0 manifest therefore declares Cursor and Devin
CLI agents; one holding only a later version declares Devin CLI alone.

## Alternatives considered

- **Keep it Cursor-only evidence** — rejected for ADR-0058's reason. Devin
  demonstrably loads it, and a later version declared no agent while holding
  components Devin loads.
- **Make only an unrecognized version Devin evidence** — rejected. Whether
  Devin reads a manifest does not depend on whether Cursor also does; a
  version-split rule would declare Devin for 1.1.0 and not for 1.0.0, which
  Devin reads in both cases.
- **Accept any `plugin.json` with any `$schema`** — rejected. Other tools ship
  a `plugin.json` with their own schema (Grafana plugins do); only the Agent
  Plugins version may be unknown.
- **Count a `.claude-plugin/plugin.json` that Devin reads as Devin evidence** —
  rejected under ADR-0052: `.claude-plugin/` is another runtime's own
  directory, so it identifies that runtime.

## Consequences

A portable plugin repository emits one BOM per kind that reads it, ADR-0058's
stated cost. A kind that later reads Agent Plugins manifests adds its own
format resolution to its evidence check.

## When to revisit

If the Agent Plugins standard gains an owning runtime, or if per-kind BOMs of
the same portable plugin stop being useful (ADR-0058's triggers).
