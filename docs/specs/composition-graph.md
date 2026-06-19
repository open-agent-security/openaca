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
- Agent components: `plugin`, `skill`, `mcp-server`, `hook`, `command`, `agent`.
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
- **`attributed_to`**: the nearest `plugin` ancestor in the lineage — a derived
  **legacy/display** projection ("via plugin X"), *not* the authoritative relationship
  (which is the containment edge). It is `None` for a package under a standalone skill,
  even though that package's real parent is the skill. Written to the BOM only as a
  migration/compat field if Fleet or existing scan consumers still need it; otherwise
  dropped from the core model.

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
- mcp-server/hook/command/agent are typically leaves in V1.

## BOM encoding (CycloneDX)

- `metadata.component` = the `target` root, with a **stable bom-ref**; the root is
  **not** duplicated in `components[]`.
- `components[]` = all non-root nodes.
- `dependencies[]` = composition edges (reusing the existing serialization): the root's
  bom-ref `dependsOn` top-level components; a plugin `dependsOn` its skills/MCPs/hooks/
  packages; a skill `dependsOn` its packages.
- Derived `openaca:scope` stays as a component property. `openaca:attributed_to` is
  written only as a legacy/compat projection (see above), not as the source of truth.
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
- **Demote `attributed_to`** from source-of-truth to a derived legacy/display field.
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

#129's containment heuristics (`_is_loadable_skill_dir`, the per-mode render
direct-deps fixes) are interim patches for this same problem. This graph **replaces**
`_classify_dep_manifest` / `_is_loadable_skill_dir` and the per-mode render
special-casing. Recommendation: **land #129** (it is converging and ships value now)
and supersede its heuristics here.

## Decisions for the companion ADR

1. Composition graph as the scanner's first-class IR, encoded in the Agent BOM via
   CycloneDX `dependencies[]` (no separate `openaca:parent`).
2. **Node key is an occurrence key** (ADR-0031), distinct from the purl match coordinate.
3. Node type is `package`; agent-/software-/transitive-dependency is a derived role.
4. Recursive descent with boundary-aware traversal (attribution by construction).
5. Containment-only attribution for V1 (defer declaration-based).
6. Single tree rooted at the scan target (`metadata.component`), not a forest.
7. Scope derived from lineage (replace `_classify_dep_manifest`); `attributed_to`
   demoted to a derived legacy/display projection.
8. Defer transitive package-dependency resolution (future `package → package` DAG /
   deps.dev) and typed edges.
