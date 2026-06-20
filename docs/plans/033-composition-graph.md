# Composition Graph (Scanner) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent composition graph (nodes + edges) the scanner's first-class IR — built by recursive descent, keyed by occurrence identity, encoded in the CycloneDX Agent BOM via `dependencies[]` — and derive scope and attribution from it, removing `_classify_dep_manifest` path heuristics and the `attributed_to` string.

**Architecture:** A new `tools/graph.py` defines `Node`/`Edge`/`Graph` with pure derivations (lineage, scope, nearest-plugin-ancestor). `tools/graph_build.py` constructs the graph by recursive descent over component-type-specific parsers, reusing today's per-manifest parsers as leaf emitters. `bom.py`, `render.py`, `matcher.py`, `sarif.py`, and `scan.py` move off `attributed_to`/`_classify_dep_manifest` to consume the graph. The change is staged strangler-fig so each commit is green: model → construction → graph-as-source-of-truth (scope + attribution derived) → BOM → render → findings/SARIF → delete the old field last.

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
- TDD: write the failing test, see it fail, implement, see it pass, commit. One logical change per commit. Follow the repo's commit conventions.
- Pre-V0, no back-compat shims for *external* consumers (ADR / `feedback_asve_no_back_compat`): change the BOM/CLI contracts directly.

## Sequencing principle: strangler-fig, delete the old model last

Every commit must be green; no stage may leave the tree red or relying on *both*
the old (`attributed_to` / `_classify_dep_manifest`) and new (graph) attribution
models at once. The order below builds the graph alongside the existing flat-ref
path, makes the graph the **single source of truth** as soon as it exists (Stage 3
derives `scope` and `attributed_to` *from the graph* and stamps them onto the refs
so today's consumers keep working unchanged), then migrates each consumer
(BOM → render → findings/SARIF) to read the graph directly, and only **deletes the
`attributed_to` field and its stamping last** (Stage 7). The transient stamping in
Stages 3–6 is migration scaffolding, not a second model: the value always comes
from the graph. This is distinct from a "back-compat shim" — there is no external
consumer being preserved; we are keeping *intermediate commits* green.

> Note on `scope`: unlike `attributed_to` (deleted at the end), `scope` stays a
> field on `ComponentRef`, but from Stage 3 on it is **always** set by the graph
> builder (`graph.scope_of`), never by a path heuristic. A graph-derived value
> cached on the ref is single-source; only `attributed_to` is removed outright.

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
    key: str                       # occurrence identity (ADR-0031); never the purl.
                                   # V1 invariant: this IS the CycloneDX bom-ref (Stage 4).
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

### Task 1.3: validate the tree invariants (single target, single parent, acyclic, endpoints exist)

The model assumes "one tree rooted at target." Enforce it so a construction bug
surfaces as a clear error, not a silent overwrite or an infinite `lineage()` loop.

**Files:**
- Modify: `tools/graph.py` (add `validate()`; make `lineage()` cycle-safe)
- Test: `tests/test_graph.py`

- [ ] **Step 1: Write the failing tests.**

```python
import pytest
from tools.graph import Node, Edge, Graph, GraphInvariantError


def test_validate_rejects_two_parents():
    g = Graph(
        nodes={k: Node(k, "package", None) for k in ("t", "a", "b", "c")}
        | {"t": Node("t", "target", None)},
        edges=[Edge("a", "c"), Edge("b", "c")],  # c has two parents
    )
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_rejects_cycle():
    g = Graph(
        nodes={"t": Node("t", "target", None), "a": Node("a", "skill", None),
               "b": Node("b", "package", None)},
        edges=[Edge("t", "a"), Edge("a", "b"), Edge("b", "a")],  # a↔b cycle
    )
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_rejects_dangling_edge_endpoint():
    g = Graph(nodes={"t": Node("t", "target", None)}, edges=[Edge("t", "ghost")])
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_rejects_zero_or_many_targets():
    g = Graph(nodes={"a": Node("a", "skill", None)}, edges=[])
    with pytest.raises(GraphInvariantError):
        g.validate()


def test_validate_rejects_disconnected_node():
    # target + a package with no path to it → not "one tree rooted at target"
    g = Graph(
        nodes={"t": Node("t", "target", None), "orphan": Node("orphan", "package", None)},
        edges=[],
    )
    with pytest.raises(GraphInvariantError):
        g.validate()
```

- [ ] **Step 2: Run; expect FAIL** (`GraphInvariantError`/`validate` undefined).

- [ ] **Step 3: Implement** `class GraphInvariantError(Exception)` and `Graph.validate()`:

```python
class GraphInvariantError(Exception):
    pass

    # --- inside Graph ---
    def validate(self) -> None:
        targets = [n for n in self.nodes.values() if n.kind == "target"]
        if len(targets) != 1:
            raise GraphInvariantError(f"expected exactly one target, found {len(targets)}")
        target_key = targets[0].key
        parents: dict[str, str] = {}
        for e in self.edges:
            if e.parent not in self.nodes or e.child not in self.nodes:
                raise GraphInvariantError(f"edge endpoint missing: {e}")
            if e.child in parents:
                raise GraphInvariantError(f"node {e.child} has multiple parents")
            parents[e.child] = e.parent
        # every node's parent-walk is acyclic AND terminates at the single target
        # (no cycles, no disconnected nodes) — i.e. exactly one tree rooted at target
        for key in self.nodes:
            seen, cur = set(), key
            while cur in parents:
                if cur in seen:
                    raise GraphInvariantError(f"cycle detected through {cur}")
                seen.add(cur)
                cur = parents[cur]
            if cur != target_key:
                raise GraphInvariantError(f"node {key} is not connected to the target root")
```

Make `lineage()` cycle-safe (guard with a `seen` set and raise `GraphInvariantError`
on revisit) so a malformed graph fails loudly instead of hanging. `build_graph`
(Stage 2) and `graph_from_cyclonedx` (Stage 4) call `validate()` before returning.

- [ ] **Step 4: Run; expect PASS.** `uv run pytest tests/test_graph.py -v`

- [ ] **Step 5: Commit.**

```bash
git add tools/graph.py tests/test_graph.py
git commit -m "graph: validate tree invariants; cycle-safe lineage"
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

- [ ] **Step 3: Implement `build_graph` skeleton + occurrence key + stable target key.** Define `occurrence_key(ref) -> str` reusing `canonical_component_identity` for components and `source_manifest + source_locator + purl` for packages (per ADR-0031, the occurrence key, not the purl alone). Create the `target` root node, then `descend(target_node, target_dir)` which for the repo root locates `.claude-plugin/plugin.json`, `**/.claude/skills/*/SKILL.md`, MCP/settings manifests, and bare dep manifests, emitting child nodes (parent = current node) and recursing. Call `graph.validate()` before returning.

> **Stable target node key (do NOT use the absolute path).** The target's node
> key / bom-ref must be a stable logical value so repo BOMs are reproducible across
> machines — an absolute filesystem path would leak into BOM identity and break
> dedup. Use a fixed logical key per mode (e.g. `"openaca:target"`, or
> `f"target/{mode}"`), and carry the resolved scan path as **source evidence only**
> (a `source_manifest`/property on the target node), never as the key. This mirrors
> ADR-0031: identity is logical, the path is evidence.

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

### Task 2.3: nested project skills + custom skill paths (repo mode)

**Files:**
- Modify: `tools/graph_build.py`
- Test: `tests/test_graph_build.py`

- [ ] **Step 1: Failing tests** for: a nested project skill `packages/frontend/.claude/skills/ui/…` found and attributed; a plugin custom `"skills": "./extras/skills/"` path resolved to its skill children.

```python
def test_nested_project_skill_found(tmp_path):
    _skill_with_dep(tmp_path, "packages/frontend/.claude/skills/ui")
    g = build_graph(tmp_path, mode="repo")
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "target"]
```

- [ ] **Step 2–4:** Extend the repo descent for nested `.claude/skills` and plugin custom skill-dir paths. Run; expect PASS.

- [ ] **Step 5: Commit.** `git commit -m "graph_build: nested/custom skill paths in repo descent"`

### Task 2.4: endpoint mode — config-seeded traversal

**Endpoint construction is NOT a filesystem descent of one directory.** Unlike repo
mode (glob the tree), the endpoint graph's roots are **seeded from resolved Claude
config**: `installed_plugins.json` (active plugins + their install/cache paths), the
settings layer stack (`enabledPlugins`, direct `mcpServers`), the project root's
`.claude/` (project skills/commands/agents), and remote MCP declarations. Recursive
descent still applies *under each seeded root* (a plugin install path descends into
its bundled components + tier-2 deps), but the **target's children come from config
resolution, not a glob**. Fold the existing `claude_install.parse_install` seed
logic (`_load_plugins_map`, `_walk_active_plugins`, settings layers, project skills,
remote MCPs) into the descent's "what are the target's children" step.

**Files:**
- Modify: `tools/graph_build.py`
- Test: `tests/test_graph_build.py`

- [ ] **Step 1: Failing test** — `build_graph(install_root, mode="endpoint", project_root=...)` against an existing `tests/test_parsers/test_claude_install.py` fixture (active plugin with a bundled skill + deps) yields the `target → plugin → skill → package` chain, and a remote MCP declared in settings appears as a direct child of `target`.

```python
def test_endpoint_active_plugin_chain(tmp_path):
    # reuse the claude_install fixture builder (installed_plugins.json + plugin
    # install path with a bundled skill bundling a dep)
    install_root, project_root = _seed_endpoint_fixture(tmp_path)
    g = build_graph(install_root, mode="endpoint", project_root=project_root)
    pkg = next(n for n in g.nodes.values() if n.kind == "package")
    assert [n.kind for n in g.lineage(pkg)] == ["package", "skill", "plugin", "target"]
```

- [ ] **Step 2–4:** Implement endpoint seeding by reusing `claude_install`'s config-resolution helpers to enumerate the target's children, then descend into each seeded root with the same boundary-aware logic as repo mode. Parent edges by construction. Run; expect PASS.

- [ ] **Step 5: Commit.** `git commit -m "graph_build: config-seeded endpoint traversal (active plugins, settings, project skills, remote MCPs)"`

---

## Stage 3 — Graph becomes the source of truth (scan builds it; scope + attribution derived from it)

**Goal:** `scan.py` (repo + endpoint) builds the graph and derives the flat ref
list, `scope`, and — transitionally — `attributed_to` **from the graph**, so
`_classify_dep_manifest` and parser-set `attributed_to` disappear while every
existing consumer (BOM/render/matcher) keeps working unchanged off the
graph-derived stamped values. The graph object is threaded alongside the refs
(unused by downstream until Stages 4–6). Green throughout. `scan_bom` is untouched
here (still reads the BOM property until Stage 4 removes it).

### Task 3.1: scan builds the graph; derive scope + attribution from it; delete `_classify_dep_manifest`

**Files:**
- Modify: `tools/scan.py` (`repo`, `endpoint`: build graph, thread it), `tools/parsers/__init__.py:113-130` (delete `_classify_dep_manifest`) + `:211-213` (delete scope stamping), `tools/parsers/claude_install.py`/`claude_plugin.py`/`claude_skill.py`/`claude_command_agent.py`/`hooks_json.py` (stop *setting* `attributed_to`; the field still exists and is now set by scan from the graph)
- Test: `tests/test_scan.py`, `tests/test_parsers/test_registry.py`

- [ ] **Step 1: Failing tests** — repo/endpoint scan builds a graph; the flat refs it produces carry **graph-derived** scope: a skill-bundled `package.json` dep is now `agent-dependency` (the ADR-0036 gap, closed by the graph) and a bare-repo dep is `software-dependency`. A skill-bundled dep's `attributed_to` equals its nearest plugin ancestor's identity (reproducing today's plugin-or-None semantics, now computed from the graph), and a standalone-skill dep's `attributed_to` is `None`.

```python
def test_scan_repo_scope_and_attribution_from_graph(tmp_path, monkeypatch):
    # plugin bundling a skill bundling a vulnerable npm dep
    _seed_plugin_skill_dep(tmp_path)  # helper builds the layout
    refs, graph = _scan_refs_and_graph(tmp_path, mode="repo")  # thin test wrapper over scan internals
    dep = next(r for r in refs if r.ecosystem == "npm")
    assert dep.scope == "agent-dependency"          # was filtered before the graph
    assert dep.attributed_to == "plugin/mp/demo@1"   # nearest plugin ancestor, from the graph
```

- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3:** In `scan.py` `repo`/`endpoint`, replace `parse_repo_grouped`+`flatten_grouped` (repo) and `parse_install` (endpoint) with `build_graph(target, mode=...)`. Project the flat ref list from the graph's non-root nodes — **`ComponentRef` is `@dataclass(frozen=True)`, so use `dataclasses.replace`, not attribute assignment**:

```python
from dataclasses import replace

def _refs_from_graph(graph):
    refs = []
    for node in graph.nodes.values():
        if node.ref is None:  # the synthetic target root has no ref
            continue
        plugin = graph.nearest_plugin_ancestor(node)
        refs.append(replace(
            node.ref,
            scope=graph.scope_of(node),
            attributed_to=(plugin.ref.component_identity if plugin and plugin.ref else None),
        ))
    return refs
```

  Delete `_classify_dep_manifest` and its scope stamping; remove the `attributed_to=` assignments from the parsers (scan now owns it via the graph). Keep `_filter_agent_scope_refs` (it reads `ref.scope`, now graph-derived). Add a `graph` parameter to the `_emit`/`match`/`build_agent_bom`/`render_*` call sites (accepted but unused downstream until later stages).

> Why stamp instead of migrate consumers now: this keeps Stages 3's commit green
> with **identical output** to today (attribution = nearest plugin, same as the old
> `attributed_to`). The *richer* skill-aware nesting arrives in Stage 5 when render
> walks edges. The only intended behavior change here is detection: skill-bundled
> deps are no longer filtered (scope is now correct), closing the ADR-0036 gap.

- [ ] **Step 4: Run; expect PASS.** Full suite green.
- [ ] **Step 5: Commit.** `git commit -m "scan: build graph as source of truth; derive scope+attribution; remove _classify_dep_manifest"`

---

## Stage 4 — BOM encoding off the graph (+ graph round-trip; rewire scan_bom)

**Goal:** `build_agent_bom` consumes the `Graph`; `metadata.component` is the target
with a **stable logical bom-ref**; `dependencies[]` are the graph edges;
`openaca:attributed_to` is gone. A new `graph_from_cyclonedx(doc) -> Graph`
reconstructs the graph from an ingested BOM (a flat ref list cannot carry edges),
and `scan_bom` uses it.

### Task 4.1: build BOM from graph; target as metadata.component; drop the property

**Files:**
- Modify: `tools/bom.py` (`build_agent_bom`, `_build_edges`, `to_cyclonedx`, `_component_properties`, `_component_ref_from_cyclonedx`)
- Test: `tests/test_bom.py`

- [ ] **Step 1: Failing tests**: (a) the target root appears as `metadata.component` with the **stable logical bom-ref** (not an absolute path) and is **not** in `components[]`; (b) `dependencies[]` reproduces the graph edges, including edges whose parent is the target's bom-ref; (c) no component carries an `openaca:attributed_to` property.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3:** Change `build_agent_bom` to accept a `Graph`.
  - **Invariant (V1): `node.key` *is* the CycloneDX bom-ref.** Every `components[]` entry's `bom-ref` and every `dependencies[]` `ref`/`dependsOn` value is the corresponding node's `key` (the occurrence identity). Edge serialization does not invent a separate mapping. State this in the BOM module docstring.
  - **Synthesize the target component.** `graph.root.ref` is `None` (the target is synthetic), so `build_agent_bom` builds `metadata.component` from the root's `key` (its stable bom-ref) and `kind` (`"target"`) — implementers must not expect `root.ref` to exist. The resolved scan path is **not** part of identity (finding: reproducibility); if recorded at all it is a non-identity property sourced from `build_agent_bom`'s existing `target`/`target_type` parameters, not the node key.
  - Build `dependencies[]` from `graph.edges` (drop the `attributed_to`→edge derivation in `_build_edges`); the target's bom-ref appears as a `dependencies[]` parent but is **not** added to `components[]`.
  - Delete the `openaca:attributed_to` property in `_component_properties` and its read in `_component_ref_from_cyclonedx`.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "bom: build from graph; target=metadata.component (stable ref); drop openaca:attributed_to"`

### Task 4.2: `graph_from_cyclonedx` round-trip; rewire `scan_bom`

A flat `component_refs_from_cyclonedx` list cannot round-trip edges. Add a
graph-aware reader and use it where a BOM is the input (`scan_bom`).

**Files:**
- Modify: `tools/bom.py` (add `graph_from_cyclonedx(doc) -> Graph`), `tools/scan.py` (`scan_bom`)
- Test: `tests/test_bom.py`, `tests/test_scan.py`

- [ ] **Step 1: Failing tests** — `graph_from_cyclonedx(build_agent_bom(g).to_cyclonedx())` reconstructs the same nodes + edges (including the `metadata.component` target as the root) and passes `validate()`; `scan_bom` of a BOM with a `plugin→skill→package` chain derives `agent-dependency` scope and "via plugin" attribution from the reconstructed graph (not from a now-absent property).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3:** Implement `graph_from_cyclonedx`: read `metadata.component` as the target node, `components[]` as nodes (kind from `component_type`), `dependencies[]` as edges; `validate()`. Rewire `scan_bom` to build the graph via `graph_from_cyclonedx` and derive scope/attribution from it (replacing the dropped property read).
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "bom: add graph_from_cyclonedx round-trip; scan_bom reconstructs the graph"`

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

## Stage 6 — Findings & SARIF off the graph

**Goal:** `Finding` no longer stores `attributed_to`; attribution is derived from the
graph at output. (Scan already builds + threads the graph from Stages 3–4.)

### Task 6.1: drop `Finding.attributed_to`; derive in matcher/sarif/finding_output

**Files:**
- Modify: `tools/matcher.py:53-59` (`Finding`), `tools/sarif.py:48-75`, `tools/finding_output.py`, `tools/render.py` (finding "via plugin" line)
- Test: `tests/test_sarif.py`, `tests/test_scan.py`

- [ ] **Step 1: Failing tests** — a finding on a skill-bundled package reports "via plugin X" derived from the graph (SARIF `attributed_to` / `component_path` and the text "path:" line); a standalone-skill finding reports the skill as the introducer (not `None`).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3:** Remove `attributed_to` from `Finding`; add a helper `attribution_for(graph, node)` used by `sarif`/`finding_output`/`render` to compute the introducer path from lineage. The matcher receives the graph so a finding's component maps back to its node.
- [ ] **Step 4: Run; expect PASS.**
- [ ] **Step 5: Commit.** `git commit -m "findings/sarif: derive attribution from graph lineage; drop Finding.attributed_to"`

---

## Stage 7 — Remove `attributed_to` from `ComponentRef` + parsers; final gate

**Goal:** The `attributed_to` field is gone from the model and every parser; nothing references it.

### Task 7.1: delete the `attributed_to` field and the transitional stamping

By now every consumer (BOM Stage 4, render Stage 5, findings/SARIF Stage 6) reads
attribution from the graph; the `ComponentRef.attributed_to` field and the
Stage-3 scan stamping that fed it are dead scaffolding. Delete them. (Parsers
already stopped *setting* `attributed_to` in Stage 3.)

**Files:**
- Modify: `tools/component_ref.py` (drop the `attributed_to` field), `tools/scan.py` (drop the `ref.attributed_to = …` stamping added in Stage 3), and any parser signature that still carries an `attributed_to` parameter purely as a pass-through (drop the param)
- Test: full suite

- [ ] **Step 1:** `grep -rn "attributed_to" tools/ tests/` → expect only the field definition, the scan stamping, and dead params/tests.
- [ ] **Step 2:** Delete the field, the scan stamping, the leftover pass-through params, and any test references.
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

- **Spec coverage:** every spec section maps to a stage — model + identity + tree invariants (S1), recursive descent + boundary traversal + occurrence dedup + stable target key + config-seeded endpoint (S2), graph-as-source-of-truth / scope+attribution derived / remove `_classify_dep_manifest` (S3), BOM encoding + `metadata.component` + drop property + graph round-trip + `scan_bom` (S4), render off edges (S5), findings/SARIF (S6), `attributed_to` removal + e2e (S7), Fleet (S8). Out-of-scope items (transitive package DAG, typed edges, declaration-based attribution) are explicitly deferred in ADR-0037 and not in any task.
- **Green at every stage (strangler-fig):** no stage deletes the old attribution model before its consumers migrate. S3 makes the graph the single source of truth (deriving scope + `attributed_to` onto refs); S4–S6 migrate consumers; S7 deletes the scaffold last. No commit relies on both models or is red.
- **Tree invariants enforced:** S1 Task 1.3 validates single-target / single-parent / acyclic / endpoints-exist and makes `lineage()` cycle-safe; `build_graph` and `graph_from_cyclonedx` both call `validate()`.
- **Occurrence vs purl:** Task 1.2 + Task 2.2 (`test_two_skills_same_purl_are_two_nodes`) pin the most important invariant — node key is the occurrence, dedup never collapses two occurrences of one purl.
- **Reproducible BOM identity:** the target node uses a stable logical bom-ref (Task 2.1 / 4.1), never an absolute path; the path is source evidence only.
- **Round-trip is graph-aware:** `graph_from_cyclonedx` (Task 4.2) reconstructs nodes + edges (the flat ref list cannot); `scan_bom` uses it.
- **No silent caps:** none introduced.
