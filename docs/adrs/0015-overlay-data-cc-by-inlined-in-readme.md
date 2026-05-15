---
id: 0015
title: Code Apache-2.0; overlay data CC-BY-4.0; license documented in README
status: accepted
date: 2026-05-15
supersedes: 0001
superseded-by: null
---

## Context

ADR-0001 set up dual licensing — Apache-2.0 for code, CC-BY-4.0 for the
data corpus — and required both licenses to ship as repo-root files
(`LICENSE` and `LICENSE-DATA`). Two follow-on facts shift the
implementation:

1. **Standard practice for similar projects.** OSV.dev,
   github/advisory-database, ossf/osv-schema, and Google's osv-scanner
   all ship a single repo-root `LICENSE` (Apache-2.0) and document any
   data-license obligations inline in the README, not in a separate
   file. A separate `LICENSE-DATA` file isn't the convention.
2. **GitHub UI signal.** GitHub's license detection (Licensee) picks up
   only one license per repo from the root `LICENSE` file. A separate
   `LICENSE-DATA` file shows as "unknown license" in the GitHub
   sidebar — confusing rather than informative.

The licensing decision itself (Apache-2.0 / CC-BY-4.0) does not change.
Only the file-structure implementation does.

## Decision

- Source code remains licensed under **Apache License 2.0**, in the
  repo-root `LICENSE` file.
- The overlay corpus (YAML files under `overlays/` and any static
  exports derived from them) remains licensed under **CC-BY-4.0**.
- The CC-BY-4.0 declaration is documented in the README's License
  section with a link to the canonical license text, not as a
  standalone `LICENSE-DATA` file at repo root.

## Alternatives considered

- **Keep `LICENSE-DATA` as a separate file** (ADR-0001's original
  approach). Rejected: not the convention in adjacent projects;
  produces an "unknown license" sidebar warning on GitHub.
- **Drop CC-BY-4.0 and apply Apache-2.0 to everything.** Rejected:
  data-license norm for OSV-compatible projects is CC-BY-4.0; matching
  it reduces friction for mirrors, aggregators, and the federation
  pipelines OpenACA expects to interoperate with.
- **Use a SPDX expression like `Apache-2.0 AND CC-BY-4.0` in
  `pyproject.toml`'s `license` field.** Rejected: SPDX expressions in
  pyproject metadata are sparsely supported by downstream tooling and
  don't replace human-readable licensing prose.

## Consequences

- README's "License" section is now the authoritative source for both
  license terms. Contributors and consumers find both licenses in one
  place.
- `LICENSE-DATA` is removed; no separate file to keep in sync.
- ADR-0001's "advisory data" terminology is also superseded — per
  ADR-0009 the corpus is overlay data, not advisories; ADR-0014
  finalized the namespace as `database_specific.openaca`.

## When to revisit

If OSV.dev relicenses its data feed away from CC-BY-4.0, or if a major
downstream consumer requires a different data license to ingest
OpenACA overlay records, revisit the data-license choice. The code
license (Apache-2.0) is permanent.
