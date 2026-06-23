---
id: 0037
title: Composition graph as the scanner's first-class IR, encoded in the Agent BOM
status: accepted
date: 2026-06-19
supersedes: "0006"
superseded-by: null
---

## Context

OpenACA's thesis is the agent **composition graph**, but the scanner never
modeled it as a first-class structure. Components were a flat list of
`ComponentRef`s; a package's relationship to its component was *guessed* by
`_classify_dep_manifest` path-shape heuristics (to set `scope`); and attribution
was a single `attributed_to` string pointing at a *plugin* or `None`, with no
intermediate skill node — the `package → skill → plugin` chain was collapsed.

PR #129 made the cost concrete: every skill/plugin layout variation
(`.claude/skills/`, plugin-bundled `skills/`, custom and nested skill paths) broke
the path heuristic, and the renderer could not nest a package under its skill
under its plugin. Each was patched and re-patched; ADR-0036 deferred skill-bundled
dependency coverage precisely because the right fix is a graph, not more
heuristics. The full design is in `docs/specs/composition-graph.md`; this ADR
records the load-bearing decisions.

## Decision

Model the agent composition as a first-class **graph (nodes + edges)** that *is*
the scanner's IR, **encoded in the Agent BOM** (CycloneDX), and derive scope and
attribution from it. Concretely:

1. **Graph is the IR, encoded in the BOM via CycloneDX `dependencies[]`.** Edges
   are composition edges (`parent dependsOn child`) using the serialization
   `bom.py` already emits. There is **no separate `openaca:parent` property** — a
   second encoding would force consumers to ask which is canonical.
2. **Node identity is an occurrence key** (`openaca:identity`, per ADR-0031),
   distinct from the **match coordinate** (purl). Two skills each declaring
   `lodash@4.17.20` are two `package` nodes with two parent edges; purl is carried
   for matching/display but is never the node key.
3. **Node type is `package`** (not `dependency`); `agent-dependency` /
   `software-dependency` / future `transitive-dependency` is a **derived role**
   from a node's lineage, not a stored type.
4. **Construction is a single recursive descent with boundary-aware traversal.**
   A component-type-specific parser locates a node's child component/package roots
   at any depth, stops at each component boundary (does not parse through a child),
   and stops at dependency manifests. Parent = the component whose subtree contains
   the child — **attribution by construction**, no separate pass, no heuristic.
5. **Attribution is containment-only for V1.** Declaration-based attribution (a
   plugin declaring a component outside its own directory) is deferred; official
   marketplace plugins contain their components.
6. **One tree rooted at the scan target.** `metadata.component` = the `target`
   (repo or endpoint) with a stable bom-ref, not duplicated in `components[]`;
   standalone skills/plugins/MCPs are top-level children. Not a forest.
7. **Scope is derived from lineage; `attributed_to` is removed.** A `package` is
   `agent-dependency` iff an agent component appears in its lineage before the
   `target` root, else `software-dependency` — replacing `_classify_dep_manifest`.
   The stored `attributed_to` field and the `openaca:attributed_to` BOM property
   are dropped; "via plugin X" becomes `nearest_plugin_ancestor(node)`, a pure
   derivation over the edges, in both the scanner and openaca-fleet. Pre-V0, no
   back-compat shim.
8. **Transitive package-dependency resolution and typed edges are deferred.**
   `package` nodes exist now and gain `package → package` out-edges later
   (resolvable via deps.dev); `openaca:edge_type` is added only if a future
   distinction requires it.

## Alternatives considered

- **Keep `attributed_to` as a denormalized parent string (status quo).** Rejected:
  it collapses the `package → skill → plugin` chain to `package → plugin-or-None`,
  loses the skill node, and is `None` under a standalone skill even though the
  skill is the real parent. The edges already carry the full chain.
- **Add an `openaca:parent` BOM property alongside `dependencies[]`.** Rejected:
  two encodings of the same relationship; consumers must decide which is canonical.
  CycloneDX `dependencies[]` is the standard mechanism and `bom.py` already emits it.
- **Key nodes by package identity / purl.** Rejected: collapses distinct
  occurrences of the same package (two skills each bundling `lodash`) into one
  node, destroying the per-occurrence attribution the graph exists to provide.
  Occurrence identity (ADR-0031) and match coordinate (purl) are separate concerns.
- **Two passes (discovery then attribution).** Rejected: a single recursive descent
  sets parent by construction (the caller is the parent), so a second attribution
  pass — and any path-shape heuristic it would need — is unnecessary.
- **Keep `_classify_dep_manifest` path heuristics.** Rejected: they are the symptom
  this graph removes; scope falls out of lineage correct-by-construction, with no
  per-layout special-casing to regress (the #129 whack-a-mole).
- **A node type named `dependency` with `transitive` flags.** Rejected: dependency
  is a *role* derived from lineage, not an intrinsic node type; `package` is the
  thing, agent/software/transitive is how it sits in the tree.
- **Forest of trees (no synthetic root).** Rejected: a single `target` root makes
  "what was scanned" explicit, gives every node a lineage that terminates, and maps
  cleanly to CycloneDX `metadata.component`.

## Consequences

- Skill-bundled dependency coverage (ADR-0036's deferred gap) falls out naturally:
  a skill's deps are `agent-dependency` because the skill is in their lineage — no
  marker, no layout special case.
- Render, findings, and SARIF derive nesting/attribution from edges; the #129
  per-mode, per-layout cases disappear.
- The BOM contract changes: `openaca:attributed_to` is gone and `metadata.component`
  gains a stable bom-ref. openaca-fleet must ingest `dependencies[]` edges and
  derive attribution on read (it reads the property at one ingest site today and
  does not ingest edges yet) — a coordinated Fleet change landing with this one.
- Cost: the parser layer is restructured from flat emission to recursive descent,
  and `ComponentRef`/`bom.py`/`render.py`/`matcher.py`/`sarif.py` all move off
  `attributed_to`. This is a large, cross-cutting change, staged in the plan.

## When to revisit

- When transitive package-dependency resolution is needed (the `package → package`
  DAG below `package` nodes; likely via deps.dev) — `package` nodes are designed to
  gain out-edges then.
- If declaration-based attribution becomes real (a plugin sourcing a component from
  outside its own directory beyond marketplace `git-subdir` provenance).
- If a consumer ever needs to distinguish composition edges from package-requires
  edges beyond what endpoint node types already imply — add `openaca:edge_type` then.
- If real external consumers of the BOM appear before this lands, revisit the
  "no back-compat shim" stance on dropping `openaca:attributed_to`.
