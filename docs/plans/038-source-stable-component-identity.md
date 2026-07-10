# Plan 038 - Source-Stable Component Identity

> **For agentic workers:** Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make component identity safe and simple for cross-BOM joins while
preserving exact occurrence lineage in the composition graph.

**Architecture:** `bom-ref` is the only occurrence key. Optional,
version-independent `openaca:identity` is the only cross-BOM join key and is
derived from a stable source namespace plus component role. Matching continues
to use typed source coordinates. Unknown-source components remain occurrence
local. ADR-0042 is the contract.

**Tech stack:** Python 3.11, pytest, CycloneDX JSON, existing graph and parser
modules. Gate: `uv run ruff check .`, `uv run ruff format --check .`,
`uv run pyright`, and full pytest.

---

## Task 1: Lock the identity contract

- [x] Add ADR-0042, supersede ADR-0029 and ADR-0030, and preserve the
  matching and occurrence/join boundaries from ADR-0031 and ADR-0038.
- [x] Record the two-key model, role qualification, source stability, optional
  identity, containment rule, and matching separation.
- [x] Add focused failing tests for package-backed MCP aliases, remote MCP
  aliases, unknown local MCPs, package role qualification, and versions.

## Task 2: Centralize source-stable identity construction

- [x] Derive package-backed MCP identity from its package source, never its
  configured alias.
- [x] Preserve URL-derived remote MCP identity independent of alias.
- [x] Omit identity for direct local components without a trustworthy source.
- [x] Qualify plugin-private child identities by the plugin namespace only when
  no independent child source exists.
- [x] Keep plugin marketplace and package identities version-independent.

## Task 3: Migrate graph and Agent BOM

- [x] Keep graph node keys and CycloneDX `bom-ref` occurrence-distinct.
- [x] Stamp graph refs with their final source-stable identity before BOM
  serialization.
- [x] Bump Agent BOM schema 0.3 to 0.4 and allow components without
  `openaca:identity`.
- [x] Update schema docs and lint rules for the optional identity property.
- [x] Prove independent occurrences keep distinct `bom-ref` values while
  sharing a sourced identity.

## Task 4: Attach occurrence-level signals by bom-ref

- [x] Add `bom_ref` to component-scoped posture and observation output.
- [x] Resolve emitted signals to the exact graph occurrence without using
  cross-BOM identity as an occurrence join.
- [x] Preserve asset-scoped findings without a component reference.
- [x] Update remote upload preparation and validation to carry `bom_ref` and
  tolerate absent identity.

## Task 5: Simplify capability lookup

- [x] Key curated capability descriptors by source-stable identity.
- [x] Remove the parallel match-coordinate lookup path made redundant by
  source-derived MCP identities.
- [x] Update the seed descriptor and corpus tests.

## Task 6: Identity lifecycle audit

- [x] Parser: expected source, version, PURL, and identity facts.
- [x] Agent BOM: schema 0.4 round-trip and optional identity.
- [x] Renderer: useful labels remain separate from identity.
- [x] OSV federation and matcher: typed source-coordinate behavior is unchanged.
- [x] Posture and observations: occurrence attachment uses `bom-ref`.
- [x] Remote upload: safe source coordinates remain redacted and exact
  occurrence references survive.
- [x] E2E: realistic plugin, MCP, skill, package, and unknown-source fixtures.

## Task 7: Final verification and delivery

- [x] Run formatting, lint, type checking, and full tests.
- [x] Re-read the diff against ADR-0042 and the identity lifecycle checklist.
- [x] Commit, push, and open a ready PR.
