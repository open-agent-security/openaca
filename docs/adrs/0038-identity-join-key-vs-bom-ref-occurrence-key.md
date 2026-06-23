---
id: 0038
title: openaca:identity is the canonical join key; bom-ref is the per-occurrence node key
status: accepted
date: 2026-06-23
supersedes: null
superseded-by: null
---

## Context

ADR-0031 and ADR-0037 both label `openaca:identity` "the agent graph occurrence
key." The composition-graph implementation (ADR-0037) makes that label
imprecise. A graph node's `key` *is* the CycloneDX `bom-ref`
(`{source_manifest}#{source_locator}#{coordinate}`, where `{coordinate}` is the
canonical identity for agent components and the package PURL for package nodes),
and that node key — not `openaca:identity` — is what is unique per occurrence. `openaca:identity` is the
canonical, type-prefixed component name (`plugin/discord`,
`mcp-server/filesystem`, `package/npm/hono`) and is intentionally **shared
across occurrences**: the same `package/npm/hono` is emitted for hono bundled
under the `discord` plugin and under the `imessage` plugin as two distinct
`bom-ref` nodes with the same identity. ADR-0037's own "two skills each
declaring `lodash@4.17.20` are two `package` nodes" example depends on this.

The 0.2.0 BOM-schema documentation surfaced the contradiction: it cannot
describe the emitted BOM accurately (identity shared, `bom-ref` per-occurrence)
while also matching the ADR-0031/0037 "identity = the occurrence key" phrasing.
A downstream BOM/Fleet consumer needs an unambiguous answer to "which field do I
join on, and which is unique per occurrence?"

## Decision

`openaca:identity` is the **canonical (logical) component identity and the
cross-occurrence join key** — stable for the same logical component across
manifests, scans, and time. It is the field consumers join on for posture,
drift, policy, inventory, and Fleet rows (the purpose ADR-0031 assigned it).

`bom-ref` (the graph node key, per ADR-0037's "node.key IS the bom-ref"
invariant) is the **per-occurrence key**, unique within a single BOM:
`{source_manifest}#{source_locator}#{coordinate}`, where `{coordinate}` is the
canonical identity for agent components and the package PURL (falling back to
name) for package nodes — so a package node's key ends in its PURL (e.g.
`…#pkg:npm/hono@4.12.5`), not its `package/<ecosystem>/<name>` identity. It
exists to wire CycloneDX `dependencies[]` composition edges; it is not a
cross-scan join key.

In flat (non-graph-backed) BOMs where an identity has a single occurrence, the
preferred `bom-ref` equals `openaca:identity`. In graph-backed BOMs they differ
whenever an identity occurs more than once or carries manifest/locator context.

This **refines, does not reverse** the prior ADRs: ADR-0031's substantive
decision (identity is the join key; matching never falls back to it) stands —
only the "occurrence key" label is corrected to "canonical / join key." ADR-0037
stands — `node.key`/`bom-ref` is the per-occurrence key, and Decision #2's
"occurrence key" label for `openaca:identity` reads as "canonical identity."
`openaca:match_coordinate` and PURL/Git matching (ADR-0031) are unaffected.

## Alternatives considered

- **Make `openaca:identity` occurrence-unique to match the prior wording.**
  Rejected: folding manifest/locator context into the identity breaks the stable
  cross-occurrence join (the same plugin in two repos would get two identities)
  and duplicates what `bom-ref` already encodes.
- **Leave the docs contradicting the ADRs.** Rejected: the BOM schema is a
  release contract for downstream consumers; ambiguity about the join key is a
  correctness bug for them.
- **Write a full supersession of ADR-0031/0037.** Rejected: their substantive
  decisions are unchanged; this is a labeling refinement, recorded as an
  amendment (INDEX-annotated, per the ADR-0017-amends-0006 precedent).

## Consequences

- `docs/openaca-bom-schema.md` and `docs/sarif-conventions.md` describe the
  emitted reality and are the contract of record for the identity/`bom-ref`
  split; both cite this ADR.
- No code change — this records existing scanner/BOM behavior.
- The ADR-0031 and ADR-0037 INDEX entries are annotated as clarified by this ADR.

## When to revisit

If a future change makes `openaca:identity` occurrence-unique (e.g. folding in
manifest context), or adds a separate cross-scan join field, supersede this ADR.
