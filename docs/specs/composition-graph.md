# Composition Graph — Design

Status: **draft** (brainstorming output; precedes the ADR + implementation plan).

## Motivation

OpenACA's thesis is the agent **composition graph**, but the scanner doesn't model
it as a first-class structure. Today:

- Components are a flat list of `ComponentRef`s.
- A dependency's relationship to its component is *guessed* by
  `_classify_dep_manifest` path-shape heuristics (to set `scope` =
  `agent-dependency` / `software-dependency`).
- Attribution is a single `attributed_to` string set to a *plugin* or `None`, with
  no intermediate skill node — the `dep → skill → plugin` chain is **collapsed**.

Consequence (observed across PR #129): every skill/plugin layout variation
(`.claude/skills/`, plugin-bundled `skills/`, custom skill paths) breaks the path
heuristic, and the renderer can't nest a dep under its skill under its plugin. Those
review findings were symptoms of a missing graph, not independent bugs.

## Goal

Model the composition as a first-class **graph (nodes + edges)** that *is* the
scanner's IR, **encoded in the Agent BOM** (CycloneDX). Derive scope and attribution
from the graph, replacing path heuristics and single-level attribution.

## Model

### Nodes

One node per discovered component or dependency. Types:

- `target` (root): the scan subject (repo, or endpoint/host). Exactly one; the tree root.
- Agent components: `plugin`, `skill`, `mcp-server`, `hook`, `command`, `agent`.
- `dependency`: a package from a dependency manifest (npm/PyPI/…), identified by purl.

Node identity: existing `component_identity` for components, purl for dependencies.
Identity is used to **dedupe** if two discovery paths reach the same artifact.

### Edges

V1 has **one edge type: `contains`** (parent → child), forming a **tree**:

- Each node has exactly one parent (its container). The structure is a single tree
  rooted at `target`; standalone skills, plugins, and direct MCP servers are
  top-level children of `target`.
- Encoded as a parent pointer per node.

Deferred (future): `depends-on` edges (package → package) for transitive dependency
resolution — a DAG layer *below* dependency nodes, resolvable via the deps.dev API.
Out of scope for V1.

### Lineage, scope, attribution (derived — not stored ad hoc)

- **Lineage** = walk parent pointers to the root.
- **Scope**: a `dependency` is `agent-dependency` iff an agent component appears in
  its lineage *before* the `target` root; `software-dependency` iff its lineage
  reaches `target` with no agent component in between. (Replaces
  `_classify_dep_manifest`.)
- **`attributed_to`** (display "via plugin X"): the nearest `plugin` ancestor in the
  lineage, derived from parent pointers. (Replaces the ad-hoc single field.)

## Construction — recursive descent

`build_graph(target_dir) -> Graph`:

1. Create the `target` root node.
2. `descend(node, dir)`: a **component-type-specific parser** for `node` finds its
   *direct* child components/deps within `dir`, creates child nodes (`parent = node`),
   and recurses into each (`descend(child, child_dir)`).
3. **Boundary handoff**: each parser scans only its own subtree and **stops at the
   next component boundary** — when it finds a child component it does *not* keep
   descending into that child's subtree itself; it hands the subtree to the child's
   parser. The root must *not* globally glob (that flattens the tree).
4. **Recursion stops at dependency manifests**: a component parser emits the
   *declared* deps in its dir as `dependency` child nodes and stops; it does not
   resolve transitive package deps (deferred).
5. **Dedup by identity**: if two paths reach the same artifact, collapse to one node.

Attribution is **by construction**: a node's parent is the component that descended
into it. No separate attribution pass; no `_classify_dep_manifest`.

Component-type parsers (V1):

- `target`/repo: direct `.claude-plugin/plugin.json` (→ plugin), `.claude/skills/*/SKILL.md`
  (→ standalone skill), MCP manifests, settings, hooks/commands/agents, and bare dep
  manifests (→ `dependency` under `target` = software-dependency).
- `plugin`: walk the plugin subtree for bundled `skills/`, `hooks/`, `commands/`,
  `agents/`, MCPs, and the plugin's own dep manifests.
- `skill`: dep manifests in the skill dir (→ `dependency` under skill = agent-dependency).
- Others as needed (mcp-server/hook/command/agent are typically leaves in V1).

## BOM encoding (CycloneDX)

- `metadata.component` = the `target` root.
- `components[]` = all nodes (flat list preserved for matching/SARIF).
- Containment edge per node via property **`openaca:parent`** = the parent's bom-ref /
  identity. Tree is reconstructable from parent pointers while the flat list stays
  queryable.
- Derived properties `openaca:scope` and `openaca:attributed_to` (nearest plugin
  ancestor) remain.
- Future `depends-on` edges → CycloneDX `dependencies[]`.

CycloneDX-native nested components are an alternative encoding; parent-pointer
properties are chosen to minimize disruption to the existing flat-component
matching/SARIF paths.

## What changes vs today

- **Remove** `_classify_dep_manifest` path heuristic — scope derives from the tree.
- **Restructure parsers**: global-glob registry → scoped recursive descent with
  boundary handoff; component-type parsers find direct children + recurse.
  `.gitignore` skipping applies per level.
- **Replace** the single `attributed_to` with explicit `openaca:parent` edges; derive
  lineage and nearest-plugin attribution from the parent chain.
- **Dedup** nodes by identity at graph-build time.
- **Render / findings / scope** derive from the graph — the #129 render nesting and
  scope cases become natural, not special-cased per mode/layout.

## Testing

Each layout becomes a graph-shape assertion:

- `.claude/skills/<name>/package.json` → `dep.parent = skill`, `skill.parent = target`;
  scope `agent-dependency`.
- plugin-bundled `<plugin>/skills/<name>/package.json` → `dep → skill → plugin → target`.
- custom `<plugin>/extras/skills/<name>/…` → same (descent finds it; no path pattern).
- bare `repo/package.json` (no agent component) → `dep.parent = target`; scope
  `software-dependency`.
- repo and endpoint modes.

## Out of scope (deferred)

- **Transitive package-dependency resolution** (`depends-on` DAG; leverage the
  deps.dev API). Dependency nodes exist now; they gain out-edges later.
- **Declaration-based attribution** (a plugin declaring a skill *outside* its own
  directory). V1 is containment-only — confirmed: official marketplace plugins
  contain their components, and the only declaration is marketplace→plugin *sourcing*
  (`git-subdir`), which is provenance, not composition.

## Relationship to PR #129

#129's containment heuristics (`_is_loadable_skill_dir`, the per-mode render
direct-deps fixes) are interim patches for this same problem. This graph **replaces**
`_classify_dep_manifest` / `_is_loadable_skill_dir` and the per-mode render
special-casing. Decision needed: land #129 as interim and let this supersede its
heuristics, or fold #129's intent into this work. Recommendation: **land #129** (it is
converging and ships value now) and supersede its heuristics here.

## Decisions for the companion ADR

1. Composition graph as the scanner's first-class IR, encoded in the Agent BOM.
2. Recursive descent (attribution by construction) over flat-discover-then-attribute.
3. Containment-only attribution for V1 (defer declaration-based).
4. Single tree rooted at the scan target (not a forest).
5. Scope derived from the tree (replace `_classify_dep_manifest`).
6. Defer transitive package-dependency resolution (future `depends-on` DAG / deps.dev).
