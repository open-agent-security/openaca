---
id: 0001
title: Code Apache-2.0; advisory data CC-BY-4.0
status: accepted
date: 2026-05-06
supersedes: null
superseded-by: null
---

## Context

ASVE ships two licensed artifacts: source code (parsers, linter, Action) and
the advisory corpus (YAML/JSON records). They have different reuse profiles
and need different licenses. The license decision is foundational — switching
later means asking every contributor to re-license their work.

## Decision

- Source code is licensed under **Apache License 2.0**.
- Advisory data is licensed under **CC-BY-4.0**, matching OSV.dev's data
  license.

## Alternatives considered

- **CC-BY-SA-4.0 for data** — rejected because the share-alike clause is
  viral on derivative works; would block downstream consumers from
  incorporating ASVE records into mixed-license outputs (the Snyk
  Vulnerability DB / GHSA / OSV.dev consumption path matters).
- **MIT for code** — shorter and equally permissive, but Apache-2.0's patent
  grant is preferred for a schema-and-tooling project that may be
  incorporated into larger systems.
- **CC0 for data** — would maximize reuse but the OSV.dev consumer base
  expects CC-BY-4.0; matching reduces friction for mirrors and aggregators.

## Consequences

- Downstream projects can mirror, ingest, and re-publish ASVE advisory data
  with attribution.
- Code contributions are subject to Apache-2.0's contributor terms.
- The dual licensing must be reflected in `LICENSE` (code) and `LICENSE-DATA`
  (advisory data) at the repo root. `LICENSE-DATA` is added when the first
  advisory lands (Plan 002).

## When to revisit

If OSV.dev relicenses its data feed away from CC-BY-4.0, or if a major
downstream consumer (Snyk, GitHub Security) requires a different data
license to ingest ASVE records, revisit the data-license choice. The code
license is permanent.
