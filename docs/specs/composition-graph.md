# Composition Graph — Design

Status: **draft** (brainstorming output; precedes the ADR + implementation plan).

## Motivation

OpenACA's thesis is the agent **composition graph**, but the scanner doesn't model
it as a first-class structure. Today:

- Components are a flat list of `ComponentRef`s.
- A package's relationship to its component is *guessed* by `_classify_dep_manifest`
  path-shape heuristics (to set `scope` = `agent-dependency` / `software-dependency`).
- Attribution is a single `attributed_to` string set to a *plugin* or `None`, with
  no intermediate skill node — the `package → skill → plugin` chain is **collapsed**.

Consequence (observed across PR #129): every skill/plugin layout variation
(`.claude/skills/`, plugin-bundled `skills/`, custom skill paths) breaks the path
heuristic, and the renderer can't nest a package under its skill under its plugin.
Those review findings were symptoms of a missing graph, not independent bugs.

## Goal

Model the composition as a first-class **graph (nodes + edges)** that *is* the
scanner's IR, **encoded in the Agent BOM** (CycloneDX, reusing the existing
`dependencies[]` edge serialization). Derive scope and attribution from the graph,
replacing path heuristics and the single-level `attributed_to`.

## Model

### Nodes

One node **per discovered occurrence** of a component or package. Types:

- `target` (root): the scan subject (repo, or endpoint/host). Exactly one; the tree root.
- Agent components: `plugin`, `skill`, `mcp_server`, `hook`, `command`, `agent`
  (node `kind` matches the existing `openaca:component_type` spelling).
- `package`: a package declared in a dependency manifest (npm/PyPI/…).

"Dependency" is **not** a node type — it is a *role* (agent-dependency,
software-dependency, future transitive-dependency) derived from a package node's
**lineage**, not from the node itself.

**Node identity is an occurrence key, not a package identity.** Per ADR-0031,
`openaca:identity` is the graph occurrence key (≈ scan target + normalized source
path + source locator + component identity), distinct from the **match coordinate**
(purl) used for advisory matching. Two skills that each declare `lodash@4.17.20` are
**two `package` nodes** with two distinct parent edges; `skill/foo` appearing once
direct and once bundled is two nodes. Purl is carried for matching/display but is
**never** the node key. Deduplication (see Construction) collapses only the *same
occurrence* reached by two discovery paths — never two occurrences of the same purl.

> **Refined by ADR-0038 (as built).** This section uses "`openaca:identity`" for
> the occurrence-key *concept*. In the emitted BOM the occurrence/node key is the
> **`bom-ref`** (`{source_manifest}#{source_locator}#{coordinate}`, where
> `{coordinate}` is the canonical identity for agent components and the package
> PURL for package nodes; this `bom-ref` IS `node.key`). The `openaca:identity`
> *property* holds the canonical, cross-occurrence identity (the short
> type-prefixed name, shared when a component appears more than once). Read
> "node key / `bom-ref`" wherever this spec says "`openaca:identity` is the
> occurrence key."

### Edges

V1 has **one edge role: composition** (`parent dependsOn child`), forming a **tree**:
each node has exactly one parent (its container); the structure is a single tree
rooted at `target` (standalone skills, plugins, direct MCP servers are top-level
children of `target`).

Edges are encoded in **CycloneDX `dependencies[]`** — the mechanism `tools/bom.py`
already uses (`edges` of `parent_bom_ref → child_bom_ref` serialized as
`{"ref", "dependsOn"}`). There is **no separate `openaca:parent` property**; a second
encoding would force consumers to ask which is canonical.

Deferred (future): transitive **package → package** edges (a DAG below `package`
nodes), resolvable via the deps.dev API. They are *also* `dependencies[]` edges —
CycloneDX's `dependsOn` is generic, so `skill dependsOn package` and `package
dependsOn package` both fit. If a consumer ever needs to distinguish composition from
package-requires beyond what endpoint node types already imply, add `openaca:edge_type`
**then** — do not start with it.

### Lineage, scope, attribution (derived — not stored as source of truth)

- **Lineage** = walk parent edges to the root.
- **Scope**: a `package` is `agent-dependency` iff an agent component appears in its
  lineage *before* the `target` root; `software-dependency` iff its lineage reaches
  `target` with no agent component in between. (Replaces `_classify_dep_manifest`.)
- **`attributed_to` is removed**, not stored. "Via plugin X" is `nearest_plugin_ancestor(node)`
  — a pure derivation over the edges, computed on demand at the point of need (render
  nesting, finding labels, SARIF). It carries no information the lineage doesn't already
  have (it was just a denormalized cache of the nearest plugin, and `None` under a
  standalone skill even though the skill is the real parent). All consumers move to the
  graph; see "What changes" for the cross-repo impact.

## Construction — recursive descent

`build_graph(target_dir) -> Graph`:

1. Create the `target` root node.
2. `descend(node, dir)`: a **component-type-specific parser** for `node` performs a
   **boundary-aware traversal** of its subtree, locating its child component/package
   *roots* (at any depth), creating child nodes (`parent = node`), and recursing into
   each (`descend(child, child_dir)`).
3. **Boundary handoff**: discovery may find a child component root anywhere in the
   subtree (so nested project skills like `packages/frontend/.claude/skills/ui-review/`
   are not lost), but a parser **stops at each component boundary** — it does not parse
   *through* a child into that child's internals, and it never assigns parentage
   heuristically. Parent = the component whose subtree contains the child.
4. **Recursion stops at dependency manifests**: a component parser emits the *declared*
   packages in its dir as `package` child nodes and stops; it does not resolve
   transitive package deps (deferred).
5. **Dedup by occurrence key** (safety net): if two paths reach the same occurrence,
   collapse to one node. Boundary ownership should make this rare; it never collapses
   distinct occurrences (different paths) that share a purl.

Attribution is **by construction**: a node's parent is the component that descended
into it. No separate attribution pass; no `_classify_dep_manifest`.

Component-type parsers (V1):

- `target`/repo: child `.claude-plugin/plugin.json` (→ plugin), `.claude/skills/*/SKILL.md`
  (→ standalone skill, at any depth), MCP/settings manifests, hooks/commands/agents,
  and bare package manifests (→ `package` under `target` = software-dependency).
- `plugin`: the plugin subtree's bundled `skills/`, `hooks/`, `commands/`, `agents/`,
  MCPs, and the plugin's own package manifests.
- `skill`: package manifests in the skill dir (→ `package` under skill = agent-dependency).
- mcp_server/hook/command/agent are typically leaves in V1.

## BOM encoding (CycloneDX)

- `metadata.component` = the `target` root, with a **stable bom-ref**; the root is
  **not** duplicated in `components[]`.
- `components[]` = all non-root nodes.
- `dependencies[]` = composition edges (reusing the existing serialization): the root's
  bom-ref `dependsOn` top-level components; a plugin `dependsOn` its skills/MCPs/hooks/
  packages; a skill `dependsOn` its packages.
- Derived `openaca:scope` stays as a component property. **`openaca:attributed_to` is
  dropped outright** (no transitional shim) — "introducing plugin" is derived from the
  edges by any consumer that needs it. The scanner and Fleet land the change together.
- Future transitive package edges → additional `dependencies[]` entries.

This reconciles ADR-0022 (Agent BOM is the composition IR; CycloneDX `dependencies[]`
carries composition edges) with ADR-0031 (occurrence identity vs match coordinate):
node key = occurrence (`openaca:identity`), match = purl, edges = `dependencies[]`.

## What changes vs today

- **Node key → occurrence** for packages too (components already use `openaca:identity`
  per ADR-0031); purl is match/display only.
- **Build the composition tree via recursive descent** (correct parent edges); reuse
  `bom.py`'s `edges`/`dependencies[]` serialization. Add `metadata.component` = target
  with a bom-ref (today the target is only an `openaca:target` property).
- **Remove `_classify_dep_manifest`** — scope derives from lineage.
- **Remove `attributed_to` entirely** (no stored field, no `openaca:attributed_to`
  property). Scanner consumers move to the edges: `render` nests by parent edges (not
  `attributed_to`); `matcher`/findings and `sarif` derive "via plugin X" from the matched
  node's lineage. **Cross-repo:** openaca-fleet (`ingest` reads the property today; plus a
  DB column, schema field, and dashboard use) migrates to ingesting the `dependencies[]`
  edges and deriving lineage — a coordinated Fleet PR. Pre-V0, so no back-compat hedge.
- **Rename node type `dependency` → `package`.**
- **Render / findings / scope** derive from the graph — the #129 render-nesting and
  scope cases become natural, not special-cased per mode/layout.

## Testing

Each layout becomes a graph-shape assertion:

- `.claude/skills/<name>/package.json` → `package.parent = skill`, `skill.parent = target`;
  scope `agent-dependency`.
- plugin-bundled `<plugin>/skills/<name>/package.json` → `package → skill → plugin → target`.
- custom `<plugin>/extras/skills/<name>/…` → same (descent finds it; no path pattern).
- nested project skill `packages/frontend/.claude/skills/<name>/…` → found and attributed.
- two skills each declaring `lodash@4.17.20` → **two** package nodes, two edges.
- bare `repo/package.json` (no agent component) → `package.parent = target`; scope
  `software-dependency`.
- repo and endpoint modes.

## Out of scope (deferred)

- **Transitive package-dependency resolution** (`package → package` DAG; leverage the
  deps.dev API). `package` nodes exist now; they gain out-edges later.
- **Declaration-based attribution** (a plugin declaring a skill *outside* its own
  directory). V1 is containment-only — confirmed: official marketplace plugins contain
  their components, and the only declaration is marketplace→plugin *sourcing*
  (`git-subdir`), which is provenance, not composition.
- **Typed edges** (`openaca:edge_type`) — add only if transitive deps require a
  distinction CycloneDX/endpoint-node-types can't already express.

## Relationship to PR #129

#129 ships the SkillSpector observation source and **skips SC4** (known-vulnerable
dependency) as a scoped limitation: OpenACA does not ingest external-scanner
vulnerability findings, and skill-bundled dependency vulnerability coverage is
deferred (ADR-0036). The interim skill-dep heuristics that were prototyped there
(`_is_loadable_skill_dir`, the `_classify_dep_manifest` `SKILL.md` extension, the
per-mode render direct-deps nesting, the `requirements.txt` parser) were **backed
out** of #129 — it now leaves the narrow plugin-marker classification untouched.

So this graph does not *supersede* anything in #129; it **adds skill-bundled-dep
coverage fresh**. Deriving scope from a node's lineage means a skill's bundled deps
fall out as agent-dependencies correct-by-construction, without the path-shape
heuristics #129 deliberately declined to add. It still replaces the pre-existing
`_classify_dep_manifest` plugin-marker rule (scope moves to lineage). When this graph
lands, the SC4 skip can be revisited only if an external-scanner vulnerability
*ingestion + deduplication* path is also built — a separate decision from the graph
itself.

## Decisions for the companion ADR

1. Composition graph as the scanner's first-class IR, encoded in the Agent BOM via
   CycloneDX `dependencies[]` (no separate `openaca:parent`).
2. **Node key is an occurrence key** (ADR-0031), distinct from the purl match coordinate.
3. Node type is `package`; agent-/software-/transitive-dependency is a derived role.
4. Recursive descent with boundary-aware traversal (attribution by construction).
5. Containment-only attribution for V1 (defer declaration-based).
6. Single tree rooted at the scan target (`metadata.component`), not a forest.
7. Scope derived from lineage (replace `_classify_dep_manifest`); **`attributed_to`
   removed** — "via plugin X" is derived on demand from the edges, in both the scanner
   and (coordinated PR) openaca-fleet.
8. Defer transitive package-dependency resolution (future `package → package` DAG /
   deps.dev) and typed edges.
