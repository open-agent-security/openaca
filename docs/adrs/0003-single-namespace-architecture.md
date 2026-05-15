---
id: 0003
title: Single namespace, type-tagged advisories
status: superseded
date: 2026-05-06
supersedes: null
superseded-by: 0009
---

## Context

OpenACA advisories cover multiple shapes: versioned-component vulnerabilities,
configuration exposures, and class-level pattern advisories. Earlier drafts
of the project plan considered splitting these into separate ID namespaces
(e.g., `OpenACA-YYYY-NNNN` for components and `OpenACA-CFG-YYYY-NNNN` for
patterns). The two-corpus approach was rejected during the brainstorm
review rounds in favor of a single namespace with a `type` discriminator.

## Decision

One ID space — `OpenACA-YYYY-NNNN`. Each record carries a `type` field with
values `vulnerability`, `exposure`, or `config`. V0 ships only
`type: vulnerability` records; the other two values are reserved in the
schema and rejected by the linter via `allOf` `not` blocks in V0 PRs.

## Alternatives considered

- **Two-corpus model** (`OpenACA-` for components, `OpenACA-CFG-` for patterns) —
  doubles consumer mental model; if a record's classification changes
  (e.g., a config issue gets a CVE), the ID has to migrate across
  namespaces, which breaks any consumer that cached the original ID.
- **Three separate namespaces** (one per type) — same problem as two
  namespaces, with more migration paths.
- **Unrestricted `type` enum** (no V0 rejection of exposure/config) —
  rejected because the methodology for exposure and config advisories is
  not yet documented; accepting them would lock in conventions without
  thinking them through.

## Consequences

- Per-type required-field enforcement happens via the schema's `allOf`
  conditional blocks and via the linter, not via namespace partition.
- New record types can be added by extending the `type` enum and adding a
  schema branch — no namespace migration.
- Consumers that read only OSV-standard fields still receive value from
  `type: vulnerability` records aliased to upstream IDs.
- The "E" in OpenACA (Exposures) is intentionally future-compatible, not V0
  scope; contributors proposing `type: exposure` advisories are rejected
  pending the methodology doc called out in the spec.

## When to revisit

If a record type emerges where the schema branch grows large enough to
warrant its own ID prefix (e.g., a third-party-only embedded record type),
revisit the single-namespace decision. Until then, the `type` discriminator
handles all foreseeable cases.
