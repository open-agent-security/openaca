# Plan 034 — MCP-server launch dependency resolution (Phase 1)

> Implements ADR-0039. Make `mcp_server` a non-leaf: resolve its launch target to a
> local dependency manifest and attach the resolved deps as `package` children, so
> they become `agent-dependency` via the existing `scope_of`. No scope-rule change,
> no mode gate. On-disk package-manager cache resolution is **Phase 2 (separate
> ADR/plan)**.

**Goal:** Close the false negative where an MCP server declared via a package-runner
launch command (`npx`/`uvx`/`bunx <pkg>`) drops its dependency supply chain because
`mcp_server` is a leaf. `DesktopCommanderMCP` (plugin declares
`npx @wonderwhy-er/desktop-commander`; root `package.json` is that package, 688 deps
currently dropped) is the canonical case.

**Architecture:** A post-descent pass over every `mcp_server` node resolves its
launch target to a directory by a single strategy — match the runner's package
**name** against a local manifest's `name` (`name_index`); everything else
(remote/external launches, local-path commands, env-wrapped/exotic launchers) →
none. It attaches that directory's deps (lockfile-preferred) under the MCP node.
Because the resolved directory's deps may already be parented to `target` (the
repo-root case), the pass **re-parents** them to the MCP node to preserve the
single-parent invariant. Launch parsing reuses `tools/identity`. On-disk
package-manager cache resolution (which would close the external-`npx` and
local-path cases) is **Phase 2 — declining beats guessing, because a wrong guess
attaches unrelated repo deps to the MCP as a false advisory.**

**Tech stack:** Python/uv. Gate: `ruff check`, `ruff format --check`, `pyright`,
`pytest`, `openaca lint`.

---

## Task 1: Local manifest `name` → directory index

**Files:**
- Modify: `tools/graph_build.py`
- Test: `tests/test_graph_build.py`

- [ ] **Step 1 — failing test.** Two manifests in a tree (`pkg-a/package.json` name
  `@x/a`, `pkg-b/pyproject.toml` name `b-tool`); assert the index maps each name to
  its directory and ignores `node_modules`/gitignored paths.

```python
def test_manifest_name_index(tmp_path):
    (tmp_path / "pkg-a").mkdir(); (tmp_path / "pkg-b").mkdir()
    (tmp_path / "pkg-a" / "package.json").write_text('{"name": "@x/a"}')
    (tmp_path / "pkg-b" / "pyproject.toml").write_text('[project]\nname = "b-tool"\n')
    idx = build_manifest_name_index(tmp_path)
    assert idx["@x/a"] == (tmp_path / "pkg-a").resolve()
    assert idx["b-tool"] == (tmp_path / "pkg-b").resolve()
```

- [ ] **Step 2 — run, confirm fail** (`build_manifest_name_index` undefined).
- [ ] **Step 3 — implement** `build_manifest_name_index(scan_root, *, include_gitignored=False)`:
  walk for `package.json` / `pyproject.toml` (reuse `iter_unignored_files`/gitignore
  context already in this module), read the `name` field (npm `name`; pyproject
  `[project].name`), map `name → dir`. On duplicate names, first wins (record nothing
  fancy; log-free). Skip `node_modules`.
- [ ] **Step 4 — run, confirm PASS.**
- [ ] **Step 5 — commit.** `feat(graph): local manifest name→dir index for launch resolution`

---

## Task 2: Launch resolver

**Files:**
- Create: `tools/mcp_launch_resolve.py` (or a section in `graph_build.py` — match
  module conventions; a small dedicated module is cleaner to test)
- Test: `tests/test_mcp_launch_resolve.py`

- [ ] **Step 1 — failing tests, one per ADR-0039 strategy:**

```python
def test_resolve_npx_name_match(tmp_path):
    idx = {"@wonderwhy-er/desktop-commander": tmp_path}
    ref = _mcp_ref(install_source="npx -y @wonderwhy-er/desktop-commander@latest")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index=idx) == tmp_path

def test_resolve_local_path_node_is_none(tmp_path):
    # `node ./dist/server.js` is NOT resolved in Phase 1, even if the path exists —
    # local-path dep resolution is Phase 2 (on-disk cache reads).
    (tmp_path / "dist").mkdir(); (tmp_path / "dist" / "server.js").write_text("//")
    (tmp_path / "package.json").write_text('{"name":"x"}')
    ref = _mcp_ref(install_source="node ./dist/server.js")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None

def test_resolve_remote_url_is_none(tmp_path):
    ref = _mcp_ref(install_source="https://mcp.example.com/mcp")
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None

def test_resolve_external_npx_is_none(tmp_path):
    ref = _mcp_ref(install_source="npx -y @playwright/mcp@latest")  # not in index
    assert resolve_mcp_launch_dir(ref, scan_root=tmp_path, name_index={}) is None
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** `resolve_mcp_launch_dir(ref, *, scan_root, name_index)`:
  - Read `install_source` from `ref.extra`. Use `identity.mcp_package_source` to
    classify the runner + package.
  - `npx`/`uvx`/`bunx <pkg>` → strip version (`@latest`/`@x.y.z`), normalize the
    bare name, look up `(ecosystem, name)` in `name_index`; return the dir or `None`.
  - Everything else → `None`: remote (`http(s)://`), external runner packages not in
    the index, local-path commands (`node <path>`, `python -m`), env-wrapped or
    exotic launchers. These are Phase 2 (on-disk cache resolution).
  - Never return a path outside `scan_root`.
- [ ] **Step 4 — run, confirm PASS.**
- [ ] **Step 5 — commit.** `feat(graph): MCP launch target → dependency dir resolver`

---

## Task 3: Post-descent attach pass (with re-parent)

**Files:**
- Modify: `tools/graph_build.py`
- Test: `tests/test_graph_build.py`

- [ ] **Step 1 — failing tests** covering the two placement cases + the invariant:

```python
def test_mcp_resolved_root_deps_reparented_from_target(tmp_path):
    # repo: subdir plugin declares npx of the ROOT package; root deps were emitted
    # under target (software-dependency). After the pass they hang off the mcp node.
    ...build fixture...
    g = build_graph(tmp_path, "repo")
    g.validate()  # single-parent invariant holds (no double parent)
    pkg = _find_package(g, "pkg:npm/<root-dep>")
    parent = g.nodes[g._parent_of()[pkg.key]]
    assert parent.kind == "mcp_server"
    assert g.scope_of(pkg) == "agent-dependency"

def test_mcp_remote_attaches_no_deps(tmp_path):
    ...remote-url MCP, root package.json present...
    g = build_graph(tmp_path, "repo")
    mcp = _find_kind(g, "mcp_server")
    assert g.children_of(mcp) == []
```

- [ ] **Step 2 — run, confirm fail.**
- [ ] **Step 3 — implement** `_attach_mcp_launch_deps(graph, scan_root, normalize, name_index, *, include_gitignored, root_dir, root_spec)`:
  - For each `node` with `kind == "mcp_server"`: `d = resolve_mcp_launch_dir(...)`; if
    `None`, continue.
  - Emit that dir's deps under the MCP node via the existing
    `_add_dep_manifest_packages(graph, node, d, ...)`.
  - **Enforce single parent:** for every package child just attached to the MCP node,
    remove any *other* parent edge (e.g. a pre-existing `target → pkg` edge from the
    root-dep walk). MCP wins. If a package is already parented to a *different*
    `mcp_server` (two servers resolving the same dir), leave it (first claim wins) and
    do not add a second edge — `_add_child` already dedups the edge, but guard the
    re-parent so it does not steal from another agent node.
  - Call `graph.validate()` is exercised by the test, not inside the pass.
- [ ] **Step 4 — wire into `build_graph`** after descent completes and before the
  return/validate: build `name_index = build_manifest_name_index(target, ...)` once,
  then `_attach_mcp_launch_deps(graph, target, normalize, name_index, ...)`. Pass the
  same gitignore/root context the descent used.
- [ ] **Step 5 — run, confirm PASS** (both tests, including `validate()`).
- [ ] **Step 6 — commit.** `feat(graph): attach MCP launch deps post-descent, re-parent root deps (ADR-0039)`

---

## Task 4: End-to-end

**Files:** `tests/test_e2e.py`

- [ ] **Step 1 — failing e2e:** DesktopCommander shape — subdir
  `plugins/claude/.claude-plugin/plugin.json` with
  `mcpServers.desktop-commander = {command: npx, args: [-y, <root-name>@latest]}`,
  root `package.json` named `<root-name>` with `@cyanheads/git-mcp-server@1.1.0`
  (→ `GHSA-3q26-f695-pp76`). Assert exit 1 and the GHSA surfaces. Add a sibling case:
  a remote-url MCP with the same root dep present asserts the advisory does **not**
  fire (root deps not attributed to a remote server).
- [ ] **Step 2 — run, confirm fail today.**
- [ ] **Step 3 — confirm PASS** after Tasks 1–3.
- [ ] **Step 4 — commit.** `test(e2e): MCP npx self-launch surfaces root-dep advisory`

---

## Task 5: Triage existing tests

**Files:** any of `tests/test_graph_build.py`, `tests/test_scan.py`, `tests/test_bom*.py`,
`tests/test_render.py`, `tests/test_e2e.py` that regress.

- [ ] **Step 1 — full suite:** `uv run pytest -q`.
- [ ] **Step 2 — triage:** an MCP server that now resolves to a local dir gains
  children; any test asserting that server is a leaf, or that its (now-resolved) deps
  are `software-dependency`/absent, updates to the new behavior. Tests for
  remote/external-npx servers must still show no attached deps — if one regressed,
  the resolver over-resolved; fix the resolver, not the test. The non-plugin-repo
  suppression test (`test_repo_software_dep_in_non_plugin_repo_is_suppressed`) must
  stay green (no MCP, no agent component → still suppressed).
- [ ] **Step 3 — re-run until green.**
- [ ] **Step 4 — commit.** `test: update for non-leaf mcp_server (ADR-0039)`

---

## Task 6: Documentation

**Files:** `docs/specs/composition-graph.md`, `docs/adrs/INDEX.md`

- [ ] **Step 1 — composition-graph.md:** update the component-parsers list — `mcp_server`
  is no longer a flat leaf; its launch target is resolved to a dependency manifest
  (runner-package name-match only) and the resolved deps are `package` children
  (agent-dependency by lineage). Add a graph-shape line to Testing: "subdir plugin +
  `mcpServers: npx <root-name>` + root `package.json` → root deps parented to the
  `mcp_server`, `agent-dependency` (ADR-0039)." Note Phase 2 (cache resolution) as
  deferred.
- [ ] **Step 2 — INDEX.md:** add the ADR-0039 entry (hook: MCP launch → dep manifest;
  un-defers a slice of ADR-0037 declaration-based attribution; on-disk cache is
  Phase 2). No amendment needed to ADR-0037 #7 — the scope rule is unchanged.
- [ ] **Step 3 — gate:** `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest -q && uv run openaca lint overlays/`.
- [ ] **Step 4 — commit.** `docs: record ADR-0039 (MCP launch dep resolution)`

---

## Self-review checklist (before PR)

- [ ] `scope_of`, the agent-scope filter, and BOM scope labels are **unchanged** —
      the fix is purely additive (MCP descent), confirmed by diff.
- [ ] `graph.validate()` passes on the DesktopCommander fixture (no double-parent).
- [ ] Remote and external-`npx` MCP servers attach **no** deps (Phase 1 boundary).
- [ ] Real spike: `uv run openaca bom repo --target <DesktopCommanderMCP>` now shows
      the root deps under the `mcp_server` node; `scan repo` surfaces their advisories.
- [ ] No host-cache reads anywhere (that is Phase 2).
