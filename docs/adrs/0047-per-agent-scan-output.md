---
id: 0047
title: One text card per agent, one machine document per scan
status: accepted
date: 2026-08-23
supersedes: null
superseded-by: null
---

## Context

ADR-0044 makes a scan emit one BOM per agent, and `docs/specs/multi-agent-support.md`
settles the BOM sink: NDJSON on stdout, one CycloneDX document per line. It also says
the renderer's `host_surface` "becomes per-agent" with "one card per agent", and — in
the same document's backward-compatibility table — that `target.host_surface` in scan
JSON output is unchanged.

Those are consistent for one agent and divergent for many, because a BOM and a findings
report are different kinds of document. A BOM's subject *is* one agent, so one document
per agent is forced. A findings report's subject is a scan: the exit code aggregates
severity across every agent, `to_sarif` writes one file, and the reference Action
contracts on one SARIF path and one exit code.

## Decision

**Text output prints one card per agent.** Each card carries that agent's Target block,
inventory tree, and next actions. With one kind registered this is one card,
structurally unchanged from today — the migration's only permitted diff is inside the
Target block itself (it gains a `coverage` row and adopts the agent's display name in
place of the hardcoded host label), and every other section is unaffected.

**Machine output stays one payload per scan.** `--format json` emits exactly one
document: the findings list stays flat, each finding carrying the agent it belongs
to, and the document gains an `agents[]` array — one entry per discovered agent with
its kind, composition source, coverage, and display label — so an agent with zero
components still appears. `target` is retained and, for a single-agent scan, is
unchanged. An exposure report (`--report exposure`) is built from the same document
and carries `agents[]` for the same reason.

`--format github` is an annotation stream rather than a document, so it gains no
`agents[]` block; the agent travels on each annotated finding. What it does gain is
per-agent attribution — `attribution_for_ref` must resolve against the graph of the
agent the finding belongs to, not a single scan-wide graph.

`stats` stays scan-wide.

## Alternatives considered

- **NDJSON for scan output too**, one report per agent, symmetric with the BOM sink —
  rejected. It splits the flat findings list the spec keeps deliberately, it leaves
  scan-wide totals with nowhere to live, and it breaks `json.load` on the multi-agent
  case for a symmetry between two documents that are not the same kind of thing. The
  BOM sink changed because a single file path genuinely cannot hold N documents; a
  findings report has no such forcing constraint.
- **The agent on each finding and nothing else** — rejected. An agent with no findings
  would then appear nowhere in machine output, losing the installed-but-unconfigured
  agent that ADR-0044's situation 18 exists to represent.
- **One card for all agents, with a per-agent Target block inside it** — rejected as a
  half-measure: the inventory tree and next actions are per-agent too, so the card
  would interleave two agents' components under one heading.

## Consequences

Machine consumers keep a single parseable document and gain a forward-compatible
`agents[]` key; `stats` remains meaningful. Text output grows linearly with agent count,
which is correct for a human surface and is one card while one kind ships.

Cost: two output shapes to reason about — per-agent for BOMs, per-scan for reports.
That asymmetry is inherent to the two document types rather than introduced here.

## When to revisit

If findings ever stop being a flat list — if the exit code or SARIF output becomes
per-agent — the report's subject has become the agent too, and this decision inverts.
