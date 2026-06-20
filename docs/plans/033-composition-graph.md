# Composition Graph (Scanner) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent composition graph (nodes + edges) the scanner's first-class IR — built by recursive descent, keyed by occurrence identity, encoded in the CycloneDX Agent BOM via `dependencies[]` — and derive scope and attribution from it, removing `_classify_dep_manifest` path heuristics and the `attributed_to` string.

**Architecture:** A new `tools/graph.py` defines `Node`/`Edge`/`Graph` with pure derivations (lineage, scope, nearest-plugin-ancestor). `tools/graph_build.py` constructs the graph by recursive descent over component-type-specific parsers, reusing today's per-manifest parsers as leaf emitters. `bom.py`, `render.py`, `matcher.py`, `sarif.py`, and `scan.py` move off `attributed_to`/`_classify_dep_manifest` to consume the graph. The change is staged so each stage is independently green: model → construction → scope → BOM → render → findings/SARIF → cleanup.

**Tech Stack:** Python 3.10+, `uv`, `pytest`, `ruff`, `pyright`. CycloneDX 1.7 BOM. Design contract: `docs/specs/composition-graph.md` and ADR-0037 (and ADR-0031 for occurrence identity vs match coordinate).

**Companion plan:** `openaca-fleet/docs/plans/011-composition-graph-ingest.md` (Fleet ingests `dependencies[]` edges and derives attribution on read; lands with the BOM contract change in Stage 5/7 here).

---

## Read before starting

- `docs/specs/composition-graph.md` — the full design (node identity, edges, lineage, recursive descent, BOM encoding, test matrix, out-of-scope).
- `docs/adrs/0037-composition-graph-ir.md` — the eight load-bearing decisions and rejected alternatives.
- `docs/adrs/0031-match-coordinates.md` — occurrence identity (`openaca:identity`) vs match coordinate (purl); the node key is the occurrence, never the purl.
- `docs/adrs/0036-defer-skill-dep-vuln-coverage.md` — this graph lands the skill-dep coverage that #129 deferred.

## File structure

| File | Responsibility | Change |
|---|---|---|
| `tools/graph.py` | `Node`, `Edge`, `Graph` dataclasses + pure derivations (`lineage`, `scope_of`, `nearest_plugin_ancestor`, `children_of`, `roots`). No I/O, no parsing. | **Create** |
| `tools/graph_build.py` | `build_graph(target, mode) -> Graph`: recursive descent, component-type parsers, boundary-aware traversal, dedup by occurrence key. | **Create** |
| `tools/component_ref.py` | `ComponentRef`: drop `attributed_to` (Stage 7). | Modify |
| `tools/parsers/__init__.py` | Remove `_classify_dep_manifest`; `parse_repo_grouped` no longer stamps scope (graph derives it). | Modify |
| `tools/parsers/claude_install.py` | Endpoint walker becomes a `graph_build` descent driver; stop setting `attributed_to`. | Modify |
| `tools/bom.py` | `build_agent_bom(graph)`; `metadata.component` = target w/ stable bom-ref; edges = graph edges; drop `openaca:attributed_to`. | Modify |
| `tools/render.py` | Tree builders walk graph edges, not `attributed_to`; "via plugin X" = `nearest_plugin_ancestor`. | Modify |
| `tools/matcher.py` | `Finding` drops `attributed_to`; attribution derived from the graph at output time. | Modify |
| `tools/sarif.py` | Derive attribution/`component_path` from graph lineage, not `finding.attributed_to`. | Modify |
| `tools/scan.py` | Build the graph once; pass it to match/render/bom. | Modify |
| `tests/test_graph.py` | Pure-derivation unit tests. | **Create** |
| `tests/test_graph_build.py` | Recursive-descent construction tests (the spec's layout matrix). | **Create** |
| `tests/test_e2e.py` | Cross-layer: a vulnerable skill-bundled dep is detected + nested correctly. | Modify |

## Conventions for every task

- `uv run` prefixes all Python. Full local gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q`.
- TDD: write the failing test, see it fail, implement, see it pass, commit. One logical change per commit.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Pre-V0, no back-compat shims (ADR / `feedback_asve_no_back_compat`): change contracts directly.

---

## Stage 1 — Graph model + pure derivations

**Goal:** A standalone `tools/graph.py` with the data model and all derivations, tested against hand-built graphs. No parsing yet.

### Task 1.1: Node / Edge / Graph dataclasses

**Files:**
- Create: `tools/graph.py`
- Test: `tests/test_graph.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph.py
from tools.graph import Node, Edge, Graph


def test_graph_roots_and_children():
    target = Node(key="target:/repo", kind="target", ref=None)
    plugin = Node(key="plugin/mp/demo@1", kind="plugin", ref=None)
    skill = Node(key="skill/deploy@1", kind="skill", ref=None)
    g = Graph(
        nodes={n.key: n for n in (target, plugin, skill)},
        edges=[Edge(parent="target:/repo", child="plugin/mp/demo@1"),
               Edge(parent="plugin/mp/demo@1", child="skill/deploy@1")],
    )
    assert g.root.key == "target:/repo"
    assert [c.key for c in g.children_of(target)] == ["plugin/mp/demo@1"]
    assert [c.key for c in g.children_of(plugin)] == ["skill/deploy@1"]
```

- [ ] **Step 2: Run it; expect ImportError / failure.**

Run: `uv run pytest tests/test_graph.py::test_graph_roots_and_children -v` → FAIL (`tools.graph` missing).

- [ ] **Step 3: Implement the model.**

```python
# tools/graph.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from tools.component_ref import ComponentRef

NodeKind = str  # "target" | "plugin" | "skill" | "mcp_server" | "hook" | "command" | "agent" | "package"


@dataclass(frozen=True)
class Node:
    key: str                       # occurrence identity (ADR-0031); never the purl
    kind: NodeKind
    ref: Optional[ComponentRef]    # None only for the synthetic target root


@dataclass(frozen=True)
class Edge:
    parent: str                    # parent node key
    child: str                     # child node key


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge] = field(default_factory=list)

    @property
    def root(self) -> Node:
        roots = [n for n in self.nodes.values() if n.kind == "target"]
        if len(roots) != 1:
            raise ValueError(f"graph must have exactly one target root, found {len(roots)}")
        return roots[0]

    def _parent_of(self) -> dict[str, str]:
        return {e.child: e.parent for e in self.edges}

    def children_of(self, node: Node) -> list[Node]:
        return [self.nodes[e.child] for e in self.edges if e.parent == node.key]
```

- [ ] **Step 4: Run it; expect PASS.** `uv run pytest tests/test_graph.py -v`

- [ ] **Step 5: Commit.**

```bash
git add tools/graph.py tests/test_graph.py
git commit -m "graph: add Node/Edge/Graph model with roots+children"
```

### Task 1.2: lineage, scope_of, nearest_plugin_ancestor

**Files:**
- Modify: `tools/graph.py`
- Test: `tests/test_graph.py`

- [ ] **Step 1: Write the failing tests** (these are the derivations the whole refactor depends on):

```python
def _build():
    # target → plugin → skill → package(lodash); target → bare package(left-pad)
    nodes = {
        "t": Node("t", "target", None),
        "p": Node("p", "plugin", _ref("plugin/mp/demo@1", "plugin")),
        "s": Node("s", "skill", _ref("skill/deploy@1", "skill")),
        "pkg": Node("pkg", "package", _ref("pkg:npm/lodash@4.17.20", "package")),
        "bare": Node("bare", "package", _ref("pkg:npm/left-pad@1.0.0", "package")),
    }
    edges = [Edge("t", "p"), Edge("p", "s"), Edge("s", "pkg"), Edge("t", "bare")]
    return Graph(nodes, edges)


def test_lineage_walks_to_root():
    g = _build()
    assert [n.key for n in g.lineage(g.nodes["pkg"])] == ["pkg", "s", "p", "t"]


def test_scope_agent_dependency_when_agent_in_lineage():
    g = _build()
    assert g.scope_of(g.nodes["pkg"]) == "agent-dependency"


def test_scope_software_dependency_when_no_agent_ancestor():
    g = _build()
    assert g.scope_of(g.nodes["bare"]) == "software-dependency"


def test_nearest_plugin_ancestor():
    g = _build()
    assert g.nearest_plugin_ancestor(g.nodes["pkg"]).key == "p"
    assert g.nearest_plugin_ancestor(g.nodes["bare"]) is None  # standalone, no plugin
```

(Add a `_ref(identity, ctype)` helper in the test building a `ComponentRef(component_identity=..., extra={"component_type": ctype})`.)

- [ ] **Step 2: Run; expect FAIL** (methods undefined).

- [ ] **Step 3: Implement the derivations.**

```python
    def lineage(self, node: Node) -> list[Node]:
        """node → ... → target root, inclusive."""
        parent_of = self._parent_of()
        chain, cur = [node], node.key
        while cur in parent_of:
            cur = parent_of[cur]
            chain.append(self.nodes[cur])
        return chain

    _AGENT_KINDS = frozenset({"plugin", "skill", "mcp_server", "hook", "command", "agent"})

    def scope_of(self, node: Node) -> str:
        """package nodes only: agent-dependency iff an agent component is an
        ancestor before the target root; else software-dependency."""
        if node.kind != "package":
            return "agent-component"
        ancestors = self.lineage(node)[1:]  # exclude self
        for anc in ancestors:
            if anc.kind == "target":
                break
            if anc.kind in self._AGENT_KINDS:
                return "agent-dependency"
        return "software-dependency"

    def nearest_plugin_ancestor(self, node: Node) -> Optional[Node]:
        for anc in self.lineage(node)[1:]:
            if anc.kind == "plugin":
                return anc
        return None
```

- [ ] **Step 4: Run; expect PASS.** `uv run pytest tests/test_graph.py -v`

- [ ] **Step 5: Commit.**

```bash
git add tools/graph.py tests/test_graph.py
git commit -m "graph: derive lineage, scope, nearest-plugin-ancestor from edges"
```

---

## Stage 2 — Recursive-descent construction

**Goal:** `build_graph(target, mode)` produces the correct tree for every layout in the spec's test matrix, reusing existing leaf parsers. Occurrence keys are the node keys; dedup never collapses two occurrences sharing a purl.

### Task 2.1: occurrence-key helper + target root

**Files:**
- Create: `tools/graph_build.py`
- Test: `tests/test_graph_build.py`

- [ ] **Step 1: Failing test** — a bare repo (one `package.json`, no agent component) yields `target → package`, scope `software-dependency`:

```python
# tests/test_graph_build.py
from tools.graph_build import build_graph


def test_bare_repo_package_is_software_dependency(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name":"app","version":"1.0.0","dependencies":{"left-pad":"1.0.0"}}'
    )
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert g.scope_of(pkg) == "software-dependency"
    assert g.lineage(pkg)[-1].kind == "target"
```

- [ ] **Step 2: Run; expect FAIL** (`tools.graph_build` missing).

- [ ] **Step 3: Implement `build_graph` skeleton + occurrence key.** Define `occurrence_key(ref) -> str` reusing `canonical_component_identity` for components and `source_manifest + source_locator + purl` for packages (per ADR-0031, the occurrence key, not the purl alone). Create the `target` root node (`key="target:<resolved path>"`), then `descend(target_node, target_dir)` which for the repo root locates `.claude-plugin/plugin.json`, `**/.claude/skills/*/SKILL.md`, MCP/settings manifests, and bare dep manifests, emitting child nodes (parent = current node) and recursing.

> Implementation note for the executor: reuse the existing leaf parsers (`package_json.parse`, `mcp_json.parse`, `claude_plugin.parse`, `claude_skill.parse`, `claude_command_agent.parse_file`, the lockfile parsers) to produce `ComponentRef`s; `graph_build` owns *placement* (which parent), the leaf parsers own *content*. Do not re-stamp `scope` on the ref — scope is derived from the graph.

- [ ] **Step 4: Run; expect PASS.**

- [ ] **Step 5: Commit.** `git commit -m "graph_build: target root + bare-package descent"`

### Task 2.2: skill-bundled deps → agent-dependency, all layouts

**Files:**
- Modify: `tools/graph_build.py`
- Test: `tests/test_graph_build.py`

- [ ] **Step 1: Failing tests** — the spec's matrix, as graph-shape assertions:

```python
def _skill_with_dep(root, rel):
    d = root / rel
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nrun\n")
    (d / "package.json").write_text('{"name":"deploy","version":"1","dependencies":{"lodash":"4.17.20"}}')
    return d


def test_claude_skills_layout(tmp_path):
    _skill_with_dep(tmp_path, ".claude/skills/deploy")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert g.scope_of(pkg) == "agent-dependency"
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "target"]


def test_plugin_bundled_skill_layout(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"demo","version":"1"}')
    _skill_with_dep(tmp_path, "skills/deploy")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "plugin", "target"]


def test_two_skills_same_purl_are_two_nodes(tmp_path):
    _skill_with_dep(tmp_path, ".claude/skills/a")
    _skill_with_dep(tmp_path, ".claude/skills/b")
    g = build_graph(tmp_path, mode="repo")
    pkgs = [n for n in g.nodes.values() if n.kind == "package"]
    assert len(pkgs) == 2  # same purl, two occurrences, two nodes
```

- [ ] **Step 2: Run; expect FAIL.**

- [ ] **Step 3: Implement** the component-type parsers for `repo`/`plugin`/`skill`: boundary-aware descent (find skill roots at any depth; a plugin parser owns its bundled `skills/`; stop at each component boundary; emit dep manifests as `package` children of the owning component). Dedup by occurrence key only.

- [ ] **Step 4: Run; expect PASS.**

- [ ] **Step 5: Commit.** `git commit -m "graph_build: boundary-aware skill/plugin descent; per-occurrence package nodes"`

### Task 2.3: nested project skills + custom skill paths + endpoint mode

**Files:**
- Modify: `tools/graph_build.py`
- Test: `tests/test_graph_build.py`

- [ ] **Step 1: Failing tests** for: a nested project skill `packages/frontend/.claude/skills/ui/…` found and attributed; a plugin custom `"skills": "./extras/skills/"` path; endpoint mode (`build_graph(install_root, mode="endpoint")` against a fixture endpoint with an active plugin) producing the same shapes as `parse_install` does today.

```python
def test_nested_project_skill_found(tmp_path):
    _skill_with_dep(tmp_path, "packages/frontend/.claude/skills/ui")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "target"]
```

(Endpoint-mode test mirrors an existing `tests/test_parsers/test_claude_install.py` fixture; assert the resulting graph has the plugin→skill→package chain.)

- [ ] **Step 2–4:** Implement endpoint descent by folding the `claude_install.py` walk logic into `graph_build` descent (active-plugins → bundled components → tier-2 deps), with parent edges by construction. Run; expect PASS.

- [ ] **Step 5: Commit.** `git commit -m "graph_build: nested/custom skill paths + endpoint-mode descent"`

---

## Stage 3 — Scope derives from lineage; remove `_classify_dep_manifest`

**Goal:** Scope is read from the graph everywhere; the path heuristic is gone.

### Task 3.1: remove `_classify_dep_manifest`, stop stamping scope

**Files:**
- Modify: `tools/parsers/__init__.py:113-130` (`_classify_dep_manifest`), `:211-213` (scope stamping)
- Test: `tests/test_parsers/test_registry.py`, `tests/test_graph_build.py`

- [ ] **Step 1: Changed test** — assert `scope_of` from the graph for the plugin-marker case (replaces `test_dep_manifest_co_located_with_plugin_classified_as_agent_dep`), and that `parse_repo` no longer assigns scope on refs (scope now lives on graph nodes).
- [ ] **Step 2: Run; expect FAIL** where old code still stamps scope.
- [ ] **Step 3: Delete `_classify_dep_manifest`** and the `replace(r, scope=...)` stamping; update `parse_repo_grouped` to return refs without scope.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "parsers: remove _classify_dep_manifest; scope derives from the graph"`

> Note: `scan.py:_filter_agent_scope_refs` (drops `software-dependency`) moves to filtering on `graph.scope_of(node)` — handled in Stage 6 when scan.py is rewired.

---

## Stage 4 — BOM encoding off the graph

**Goal:** `build_agent_bom` consumes a `Graph`; `metadata.component` is the target with a stable bom-ref; `dependencies[]` are the graph edges; `openaca:attributed_to` is gone. Round-trips through `to_cyclonedx`/`from_cyclonedx`.

### Task 4.1: build BOM from graph; target as metadata.component

**Files:**
- Modify: `tools/bom.py` (`build_agent_bom`, `_build_edges`, `to_cyclonedx`, `_component_properties`, `_component_ref_from_cyclonedx`)
- Test: `tests/test_bom.py`

- [ ] **Step 1: Failing tests**: (a) the target root appears as `metadata.component` with a stable bom-ref and is **not** in `components[]`; (b) `dependencies[]` reproduces the graph edges; (c) no component carries an `openaca:attributed_to` property; (d) `component_refs_from_cyclonedx(doc)` round-trips node/edge structure.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3:** Change `build_agent_bom` to accept a `Graph`; emit `metadata.component` from `graph.root`; build `dependencies[]` from `graph.edges` (drop the `attributed_to`→edge derivation in `_build_edges`); delete the `openaca:attributed_to` property in `_component_properties` and its read in `_component_ref_from_cyclonedx`.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "bom: build from graph; target=metadata.component; drop openaca:attributed_to"`

---

## Stage 5 — Render off the graph

**Goal:** Tree nesting and "via plugin X" come from graph edges, not `attributed_to` string-matching.

### Task 5.1: rewrite tree builders to walk edges

**Files:**
- Modify: `tools/render.py` (`render_inventory_tree`, `render_repo_inventory_tree`, `_build_plugin_node`, `_build_repo_plugin_node`, `_build_direct_node`, `_bundled_categories`, `_tier2_summary`, `_direct_categories`)
- Test: `tests/test_render.py`

- [ ] **Step 1: Failing tests** — rebuild the existing tree-shape tests to pass a `Graph` (or refs + graph) instead of relying on `attributed_to`: plugin nests its skills; a skill nests its package deps (the coverage #129 deferred, now natural); standalone skill's deps nest under the skill; bare repo software-deps suppressed.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3:** Have the render entry points accept the `Graph`; replace `attributed_to`-filtering in `_bundled_categories`/`_tier2_summary`/`_direct_categories` with `graph.children_of(node)` grouped by child kind; derive the "via plugin X" label from `graph.nearest_plugin_ancestor`.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "render: nest inventory tree from graph edges; via-plugin from lineage"`

---

## Stage 6 — Findings & SARIF off the graph; rewire scan.py

**Goal:** `Finding` no longer stores `attributed_to`; attribution is derived from the graph at output. `scan.py` builds the graph once and threads it.

### Task 6.1: scan.py builds the graph and threads it

**Files:**
- Modify: `tools/scan.py` (`repo`, `endpoint`, `scan_bom`; `_filter_agent_scope_refs`)
- Test: `tests/test_scan.py`

- [ ] **Step 1: Failing test** — repo/endpoint scan builds a graph, filters `software-dependency` via `graph.scope_of`, and passes the graph to `match`, `render_*`, and `build_agent_bom`. For `scan_bom`, reconstruct the graph from the ingested BOM edges.
- [ ] **Step 2–4:** Implement: replace `parse_repo_grouped`+`flatten_grouped`+`_filter_agent_scope_refs` with `build_graph` + graph-derived scope filtering; thread the graph. Run; expect PASS.
- [ ] **Step 5: Commit.** `git commit -m "scan: build composition graph once and thread it through match/render/bom"`

### Task 6.2: drop `Finding.attributed_to`; derive in matcher/sarif/finding_output

**Files:**
- Modify: `tools/matcher.py:53-59` (`Finding`), `tools/sarif.py:48-75`, `tools/finding_output.py`, `tools/render.py` (finding "via plugin" line)
- Test: `tests/test_sarif.py`, `tests/test_scan.py`

- [ ] **Step 1: Failing tests** — a finding on a skill-bundled package reports "via plugin X" derived from the graph (SARIF `attributed_to` / `component_path` and the text "path:" line); a standalone-skill finding reports the skill as the introducer (not `None`).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3:** Remove `attributed_to` from `Finding`; add a helper `attribution_for(graph, node)` used by `sarif`/`finding_output`/`render` to compute the introducer path from lineage.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "findings/sarif: derive attribution from graph lineage; drop Finding.attributed_to"`

---

## Stage 7 — Remove `attributed_to` from `ComponentRef` + parsers; final gate

**Goal:** The `attributed_to` field is gone from the model and every parser; nothing references it.

### Task 7.1: delete the field and all assignments

**Files:**
- Modify: `tools/component_ref.py` (drop `attributed_to`), `tools/parsers/claude_install.py`, `claude_plugin.py`, `claude_skill.py`, `claude_command_agent.py`, `hooks_json.py` (drop `attributed_to=` assignments)
- Test: full suite

- [ ] **Step 1:** `grep -rn "attributed_to" tools/ tests/` → expect only the soon-to-be-removed sites.
- [ ] **Step 2:** Delete the field + all assignments + the parser params that only carried it (where the graph now sets parentage). Where a parser took `attributed_to` purely to stamp it, drop the param.
- [ ] **Step 3:** `grep -rn "attributed_to" tools/ tests/` → expect **(none)**.
- [ ] **Step 4: Full gate green.** `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q`
- [ ] **Step 5: Commit.** `git commit -m "model: remove attributed_to entirely (graph is the source of parentage)"`

### Task 7.2: e2e — vulnerable skill-bundled dep is detected and nested

**Files:**
- Modify: `tests/test_e2e.py`
- Test: same

- [ ] **Step 1: Failing test** — a fixture repo with a skill bundling a known-vulnerable `package.json` dep: scan finds the advisory, the inventory tree nests the package under the skill, scope is `agent-dependency`, and SARIF attributes it "via" the skill/plugin. (This is the product promise the graph unlocks vs ADR-0036's deferred gap.)
- [ ] **Step 2–4:** Implement against the real corpus + parser/exporter; run; expect PASS.
- [ ] **Step 5: Commit.** `git commit -m "e2e: vulnerable skill-bundled dependency is detected and correctly nested"`

---

## Stage 8 — Companion: Fleet edge ingestion (separate repo/plan)

The BOM contract change (Stage 4 drops `openaca:attributed_to`; Stage 5/7 finalize edges as the only attribution carrier) must land with the Fleet migration. See `openaca-fleet/docs/plans/011-composition-graph-ingest.md`:

- Ingest `dependencies[]` into a `bom_dependencies` edge table (alembic migration + model).
- Stop reading `openaca:attributed_to`; derive "via plugin X" on read from edges (nearest plugin ancestor).
- Replace the stored `attributed_to` column/API field with the derived value; update TS types.
- Build the deferred dashboard attribution display from the derived value.
- Drop the `attributed_to` column; update fixtures/tests.

**Coordination:** do not merge the scanner's `openaca:attributed_to` removal (Stage 4) to a `main` that Fleet's production reads from until the Fleet PR is ready; pre-V0 there are no other consumers.

---

## Self-review checklist (run before handing off)

- **Spec coverage:** every spec section maps to a stage — model+identity (S1), recursive descent + boundary traversal + occurrence dedup (S2), scope-from-lineage / remove `_classify_dep_manifest` (S3), BOM encoding + metadata.component + drop property (S4), render off edges (S5), findings/SARIF + scan rewire (S6), `attributed_to` removal (S7), Fleet (S8). Out-of-scope items (transitive package DAG, typed edges, declaration-based attribution) are explicitly deferred in ADR-0037 and not in any task.
- **Occurrence vs purl:** Task 1.2 + Task 2.2 (`test_two_skills_same_purl_are_two_nodes`) pin the most important invariant — node key is the occurrence, dedup never collapses two occurrences of one purl.
- **No silent caps:** none introduced.
