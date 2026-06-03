---
id: 0028
title: Expose domain logic via a curated `openaca.core` consumption facade
status: accepted
date: 2026-06-03
supersedes: null
superseded-by: null
---

## Context

OpenACA's domain logic — agent component identity (`ComponentRef`), Agent BOM
parsing/building, OSV query planning, advisory matching, severity
normalization, and risk attribution — lives under `tools.*` as internal
modules. The reference CLI imports those modules directly.

Other tools build on this domain layer. The Fleet upload path (ADR-0024) and
its backend are the first such consumer: a hosted service ingests Agent BOMs
and must match them against advisories using the *same* semantics the CLI uses,
so that what a consumer reports agrees with what `openaca scan` reports.

When a consumer reimplements these semantics, or imports `tools.*` internals
directly, it drifts. This is not hypothetical:

- ADR-0027 changed OSV query planning so that GitHub and Docker source
  identities are no longer plain PURL queries (commit queries, `GIT`
  package/version queries, Docker skipped). A consumer that still queries every
  component PURL silently misses GitHub-source advisories the CLI finds.
- Severity normalization (CVSS parsing, `MODERATE` → `MEDIUM`) is non-trivial
  domain logic; a separate implementation diverges the moment either side
  changes.

`tools` is also a poor name to expose as an installable public import surface,
and the module boundaries inside `tools.*` change freely as the scanner
evolves. There is currently no *supported* surface a downstream consumer can
depend on without coupling to internals.

## Decision

Introduce `openaca.core` as a **curated facade** that re-exports the specific
domain APIs downstream consumers need. Consumers depend **only** on
`openaca.core` and pin OpenACA by version or commit SHA.

The facade re-exports a deliberately minimal, named surface (not a wildcard
re-export). The supported surface is the domain logic a consumer needs to go
from a stored CycloneDX BOM to advisory findings with CLI-equivalent semantics:

- `ComponentRef`
- `build_agent_bom`, `component_refs_from_cyclonedx`
- OSV query planning: `collect_osv_queries`, `OsvQuery`, and the query
  provenance stamping/filtering helpers the matcher depends on
- `match`
- severity normalization helpers
- risk attribution / containment-path helpers

The internal modules stay under `tools.*` for now; the facade wraps/re-exports
them. Internals may later migrate from `tools.*` to `openaca.*` without
changing the facade surface, so consumers are insulated from that move.

**Principle:** a downstream consumer must not reimplement OpenACA domain
semantics — identity, BOM parsing, query planning, matching, severity
normalization, or attribution. Those are owned by OpenACA and consumed through
the facade. Consumers own their own persistence, storage, fetch transport, auth,
and workflow.

**Stability contract:** `openaca.core` is the cross-consumer *consumption seam*,
**not a stable public API pre-V0**. There is no back-compat guarantee yet
(consistent with the pre-V0 stance). Breaking changes are allowed and are
surfaced through the consumer's pin plus a contract test, not through
back-compat shims. A consumer pins a version/SHA and upgrades intentionally; its
contract test (a fixture BOM exercising npm, GitHub commit, Git tag, and Docker
components) catches a break during the upgrade rather than in production.

## Alternatives considered

- **Consumers reimplement the domain semantics**: rejected. Guarantees drift;
  already observed (PURL-only query planning and a parallel CVSS/severity
  implementation diverging from ADR-0027 semantics). Every change on either
  side reopens the gap.
- **Consumers import `tools.*` internals directly**: rejected. Couples
  consumers to modules that change freely, with no supported surface, so any
  internal refactor risks silent breakage. `tools` is also a collision-prone
  top-level name to ship as a public import.
- **Full `tools/` → `openaca/*` namespace migration as the first step**:
  rejected as the opening move. It is a large cross-cutting rename touching
  every CLI import and any in-flight branches, with high churn and merge risk,
  and it is not required to establish the seam. The facade delivers the
  anti-drift benefit immediately; the internal migration can follow later and
  be absorbed transparently behind the facade.

## Consequences

- A small `openaca/core/` package re-exports the curated symbols above; the CLI
  may migrate to import through the facade over time, but is not required to as
  part of introducing it.
- The matcher's reliance on query provenance (the `osv_query_matches` stamping
  from ADR-0027) means the stamping/filtering helpers are part of the supported
  surface: a consumer that fetches advisories itself must stamp records in the
  same shape before calling `match`.
- Consumers map matched `ComponentRef`s back to their own stored rows by
  CycloneDX `bom-ref` (the stable per-component key), not by list order.
- Because the facade carries no stability guarantee pre-V0, the discipline that
  prevents regressions is the consumer-side pin + contract test, not API
  versioning.

## When to revisit

Revisit when OpenACA reaches a public-stability milestone and a versioned,
semver-guaranteed API is warranted; when the internal modules migrate from
`tools.*` to `openaca.*` (the facade should absorb that move); or when multiple
independent external consumers need formal stability guarantees rather than a
pinned seam.
