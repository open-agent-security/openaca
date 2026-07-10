# Plan 039 - Shared Exposure Contract

**Goal:** Make CLI and downstream consumers use one component exposure model,
grouping rule, ranking engine, action vocabulary, and explanation logic.

**Architecture:** ADR-0043 defines cards with a logical component and exact BOM
occurrences. Scan paths carry `bom_ref` and optional source-stable identity.
`openaca.core` exports the engine and types; downstream consumers add only
their own scope metadata.

**Gate:** Ruff, Pyright, full pytest, facade contract tests, and a generated
scan/report end-to-end test.

## Tasks

- [x] Replace mixed `component_id` semantics with `component` plus
  `occurrences`.
- [x] Group locally by non-null identity, otherwise exact `bom-ref`.
- [x] Preserve multiple composition paths and evidence occurrence references.
- [x] Enrich scan path nodes with `bom_ref`, identity, and observed version.
- [x] Keep asset-scoped posture outside component exposure cards.
- [x] Route CLI report rendering through the shared exposure model.
- [x] Export the model, engine, and renderer through `openaca.core`.
- [x] Update the public spec and command examples.
- [x] Run all quality gates and inspect the final contract diff.
- [x] Commit, push, and open a ready PR based on the identity PR branch.
