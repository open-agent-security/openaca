---
id: 0040
title: Separate scan evidence from triage decisions
status: accepted
date: 2026-07-03
supersedes: null
superseded-by: null
---

## Context

OpenACA scan output has become substantially more useful: it shows endpoint
scope, inventory trees, component paths, vulnerability findings, posture
findings, observations, and next commands. That is still a scan result. It is
optimized for evidence and machine consumption, not for a security or platform
lead asking which agent component deserves attention first.

Discovery feedback exposed the gap. Users do not want a wall of findings; they
want a small set of ranked components, why those components matter in agent
context, and what action to take. At the same time, `scan` should remain a clear
evidence-collection command. If scan starts owning every prioritization and
workflow concern, the CLI boundary becomes blurry and future downstream
consumers will reimplement the same logic differently.

The plausible alternative is to add more flags to `scan` and treat reports as
just another output format. That gives a convenient one-command path, but it
does not name the decision layer that the Claude Code plugin, concierge
assessments, and other downstream consumers all need to share.

## Decision

OpenACA separates **scan** from **triage**. `openaca scan` collects evidence:
inventory, Agent BOM composition, advisory matches, posture findings,
observations, and structured scan output. `openaca triage` consumes scan
evidence and produces component-centric exposure cards and reports that rank,
explain, and recommend action.

The user-friendly path may remain a single command:

```bash
openaca scan endpoint --report exposure --output openaca-exposure-report.md
```

That form is syntactic sugar for "scan, then triage this scan result." The
composable path is explicit:

```bash
openaca scan endpoint --format json > openaca-scan.json
openaca triage openaca-scan.json --report exposure --output openaca-exposure-report.md
```

Both paths use the same triage engine. The Claude Code plugin and any other
downstream consumer should call that shared engine instead of inventing report
logic.

## Alternatives considered

- **Only add `--format markdown` to `openaca scan`**: rejected because a
  forwardable exposure report is not just syntax. It ranks components, explains
  why they matter, chooses one recommended action, and carries scope caveats.
  Calling that plain formatting hides the decision layer.
- **Make `triage` the only way to produce reports**: rejected because the common
  first-run workflow should stay one command. Users should not need to learn
  scan-artifact plumbing before they can produce a local report.
- **Build report generation only in a hosted consumer**: rejected because local
  OSS and the Claude Code plugin need a forwardable single-endpoint report on
  their own; a report layer should not require a hosted backend. Any consumer
  that aggregates triage results across endpoints should reuse the same triage
  model, not define a separate one.
- **Embed triage output in the Agent BOM**: rejected because ADR-0022 keeps the
  BOM composition-only. Triage depends on scan findings and interpretation; it
  is scan report data, not BOM identity or composition data.
- **Use vulnerability severity as the complete rank**: rejected because severity
  ranks findings, not agent components. Triage must account for active status,
  composition lineage, posture, confidence, and actionability when that evidence
  exists.

## Consequences

The CLI gains a new decision layer and a clearer mental model:

- `openaca scan ...` answers "what is present and what matched?"
- `openaca triage ...` answers "what should I look at first and what should I do?"

Implementation needs a structured scan artifact that carries enough evidence
for triage without re-reading the target. The first triage engine should be
deterministic and conservative; it must not invent capability or runtime facts.
Capability extraction can improve triage later, but missing capability facts
should produce honest lower-confidence cards, not overclaims.

This creates one shared decision layer instead of several: the CLI produces a
single-endpoint Markdown report, and the Claude Code plugin exposes the same
report. Any consumer that needs to aggregate triage results across multiple
endpoints should call the same triage engine and reuse its card model rather
than defining a separate ranking or report format.

## When to revisit

Revisit if scan artifacts cannot practically carry enough evidence for useful
triage, if a downstream aggregating consumer requires materially different
ranking semantics from local reports, or if the CLI surface becomes confusing
enough that maintaining both `scan --report exposure` and `triage` harms
usability.
