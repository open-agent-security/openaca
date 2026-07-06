---
id: 0041
title: Model agent-component capabilities as first-class descriptors, extracted defensibly
status: accepted
date: 2026-07-03
supersedes: null
superseded-by: null
amends: 0022
---

## Context

The composition graph tells us what an agent is built from and how it is wired,
and advisory matching tells us which components carry known vulnerabilities. What
it cannot yet express is *what a component can do* — read files, run shells,
egress to the network, touch credentials. That capability signal is the missing
input for reasoning about which component is actually risky and why ("the
vulnerable package sits inside a plugin whose MCP server can run shells and read
credentials"), rather than only "a vulnerable package exists."

Today a few capability-adjacent facts exist as scattered posture findings (skill
`allowed-tools`, insecure transport, mutable install) with no shared model, no
place on the graph node, and no consistent evidence/confidence contract.

Extracting capabilities is a trap in two directions. Inferring them from
open-ended component source is the same heuristic-over-open-ended-input failure
mode ADR-0039 rejected — a guesser produces false descriptors, and a false
"this MCP reads your credentials" is worse than a gap. And enumerating an MCP
server's tools requires *starting the server* — a runtime action that executes
the untrusted component under assessment. A capability model has to be honest
about what it does not know.

## Decision

OpenACA models capabilities as **first-class, evidence-backed descriptors** on
graph nodes, governed by four rules.

1. **Capabilities are descriptors, separate from risk modifiers.** A closed
   capability taxonomy — `file_read`, `file_write`, `shell_exec`,
   `network_egress`, `credential_access`, `sensitive_data_access` (extended only
   by ADR) — states *what a component can do*. Context that changes how much a
   capability matters (`execution_locus`, transport, auth, install mutability,
   active, operator) is modeled separately as risk modifiers, not folded into the
   capability list.

2. **Absence is not falsehood; declining beats guessing.** Every component
   carries a `capability_coverage` marker (`unknown | partial | complete`); most
   extraction is `partial`. A non-empty list is not proof of absence, and an
   uncovered component is `unknown`, never silently empty. A capability is
   asserted only with citable evidence.

3. **Claim type is orthogonal to source (per ADR-0035).** Each capability records
   `method` (`declared | curated | inferred`) separately from `source`
   attribution. Extraction is tiered: declared (manifest-stated), curated
   (reviewed corpus), inferred (bounded deterministic source analysis, deferred).
   Model-drafted reads are a manual-review aid only and are never emitted directly
   as capability claims.

4. **Capabilities live in an identity-keyed corpus, version-independent.** Curated
   capability records live in `capabilities/<identity>.yaml`, keyed by
   `openaca:identity` (ADR-0038), distinct from the advisory `overlays/`. Because
   capabilities are a coarse, near-monotonic behavioral property, records are
   version-independent by default (one per identity, not a `(package, version)`
   matrix), with a `last_reviewed` stamp so a report can flag drift. Per-capability
   version ranges are deferred, not a v1 schema field: enforcing "this capability
   only applies from version X on" needs ecosystem-aware version comparison, and
   corpus identities are not all PURL-shaped (ADR-0038 keys them
   version-stripped by design). Real drift-catch — checking a curated record
   against the installed version — is tier-3 (source-analysis) territory. A local
   MCP config alias is user-chosen, not upstream-verified, so a record scoped to
   a specific package declares a `match_coordinate` that **replaces** identity as
   the lookup key for that record rather than supplementing it — otherwise an
   unrelated component reusing the curated identity string as its own local
   alias would inherit capabilities it was never reviewed for.

MCP tool-list enumeration is out of scope: it requires running the server.
Declared tool descriptions, where statically present, are attacker-controllable
evidence, not ground truth.

## Relationship to ADR-0022 (amendment)

ADR-0022 keeps the Agent BOM composition-only (components, source provenance,
identities, edges; findings/posture excluded). This ADR amends that boundary:
**capability facts are component descriptors** — the same category as identity
and provenance — and are emitted in the BOM as `openaca:capabilities` /
`openaca:capability_coverage` metadata. The composition-only principle stands:
**exposure scores, rankings, and "why it matters" cards are derived decisions
and remain outside the BOM**, referencing BOM components the way findings
already do. Adding `openaca:capabilities` bumps `OPENACA_BOM_SCHEMA_VERSION`
(0.2 → 0.3).

## Relationship to ADR-0040 (scan/triage boundary)

ADR-0040 separates scan evidence from triage decisions: `openaca triage` consumes
scan artifacts to rank and explain component-centric exposures, and its reports
stay outside the BOM. This ADR is complementary and consistent: **capabilities
are the descriptor input that triage consumes** for its "why it matters" and
ranking. The two boundaries agree — capability *facts* are BOM descriptors (this
ADR); capability-informed *exposure decisions and reports* are triage output
(ADR-0040), outside the BOM. Per ADR-0040, the first triage engine is
deterministic and must not invent capability facts; it uses whatever capabilities
this layer provides and treats the rest as `unknown`.

## Consequences

- The graph node / `ComponentRef` gains an optional capability block; the exposure
  layer (specified separately) consumes it. Scan/BOM stays the evidence layer;
  exposure is the decision layer.
- A new `capabilities/` corpus + schema + linter must be maintained, on the same
  discipline as overlays.
- Coverage is honest by construction (`unknown`/`partial`), so early reports will
  under-claim rather than over-claim — an intentional trade of recall for
  defensibility.
- The corpus grows as records are reviewed; the long tail is `unknown` until then.

## Alternatives considered

- **Leave capabilities as scattered posture findings.** Rejected: no shared model
  means the exposure layer cannot reason about capability uniformly, and there is
  no evidence/confidence/coverage contract.
- **One flat "risk attributes" bag** mixing capabilities and modifiers. Rejected:
  "reads local files" and "unauthenticated HTTP to operator X" are different claim
  types; conflating them muddies both ranking and reporting.
- **Infer capabilities broadly via source heuristics / model classification to
  maximize coverage.** Rejected as the default (the ADR-0039 trap): unverifiable,
  manipulable via attacker-controlled descriptions, and false descriptors are
  worse than gaps. Source analysis is allowed only as a bounded, evidence-lined,
  deferred tier; model output only as a manual-review drafting aid.
- **Enumerate MCP tools by starting the server.** Rejected: runtime action that
  executes the untrusted component under assessment.
- **Per-`(package, version)` capability records.** Rejected: advisory-granularity
  applied to a coarse, slowly-changing property — a maintenance explosion that is
  mostly redundant.
- **Fold capabilities into advisory overlays.** Rejected: different version
  semantics, review cadence, and evidence model.
- **Emit exposure scores/decisions in the BOM.** Rejected: violates ADR-0022's
  composition-only boundary; the BOM stays evidence, not decisions.
