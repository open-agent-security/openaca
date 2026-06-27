---
id: 0039
title: Resolve MCP-server launch targets to their dependency manifests (declaration-based attribution for MCP)
status: accepted
date: 2026-06-25
supersedes: null
superseded-by: null
---

## Context

`mcp_server` is a **leaf** in the composition graph today. `descend()` has
branches only for `target`, `plugin`, and `skill` (`tools/graph_build.py`); there
is no `mcp_server` branch, and the code says so (`graph_build.py`: "`mcp_server`
leaf children of the target, no descent"; spec `docs/specs/composition-graph.md`
line 126). The reason is structural: an MCP server is discovered from a
**`mcpServers` declaration** in a manifest (`plugin.json` / `.mcp.json` /
`settings.json` / `claude_desktop_config.json`) — a **launch command or URL**, not
a directory. Unlike a skill or plugin, the node has no associated source directory
for the descent to walk, and nothing resolves its launch target back to where the
launched code and its dependency manifest live. So the server's supply chain is
never attributed and is dropped from matching/federation/rendering/the BOM.

`DesktopCommanderMCP` is the canonical miss: its plugin declares
`{"command": "npx", "args": ["-y", "@wonderwhy-er/desktop-commander@latest"]}`, the
repo root `package.json` is `name: @wonderwhy-er/desktop-commander` with a
688-package lockfile, and the `mcp_server` node is created but — being a leaf —
gets none of those deps.

ADR-0037 deferred **declaration-based attribution** (a component whose
implementation/deps are declared to live outside its own directory). This ADR
un-defers a **scoped slice** of it: resolving an MCP server's launch target to a
local dependency manifest. It does **not** change the lineage-derived scope rule
(ADR-0037 #7): once the resolved deps hang off the `mcp_server` node, they have an
agent-component ancestor and are `agent-dependency` via the *existing* `scope_of`.

A corpus measurement (136 community plugin repos, 108 MCP servers) sized the gap
and guarded against over-reach:

- 53 launch via `npx`/`uvx` a **named package**, of which only **8** match a local
  manifest's `name` (the repo *is* the package, DesktopCommander-style); the other
  **45** launch an **external** published package whose source is not in the repo.
- **10** launch a **local path** (`node ./dist/server.js`, `python -m ...`).
- **30** are **remote** (`url`/http/sse) — nothing executes locally.
- 15 other/unknown.

So a precise, declaration-following resolver attributes deps in ~18 of 108 servers
in repo mode (8 self-match + 10 local-path), correctly attributes **nothing** to
the 45 external + 30 remote, and confirms the headline finding: the agent-scope
advisory count was **mostly correct**, not grossly undercounted — the genuine
false-negative class (repo *is* the launched package, or launches a local path) is
real but bounded.

## Decision

**Make `mcp_server` a non-leaf: resolve its launch target to a dependency-manifest
location and attach the resolved deps as `package` children of the `mcp_server`
node.** Resolution strategies, tried in order, Phase 1:

1. **Local manifest name-match.** Parse the launch with the existing
   `tools/identity` helpers (`launcher_and_args`, `unpinned_mcp_package` /
   `_extract_mcp_package_from_args`). If it is `npx`/`uvx`/`bunx <pkg>` and `<pkg>`
   equals the `name` of a `package.json`/`pyproject.toml` found in the scan tree,
   emit that manifest's directory deps (lockfile-preferred, via the existing
   `_add_dep_manifest_packages`).
2. **Local path.** If the command launches an on-disk path within the scan root
   (`node ./dist/server.js`, a script path, `python -m <local module>`), emit the
   nearest dependency manifest at/above that path.
3. **Remote (`url`/http/sse).** No children — nothing executes locally (correct).
4. **Unresolved** (external published package not present locally) → no children.
   The launched package itself is still advisory-matched via its launch coordinate
   (ADR-0031); its *transitive closure* needs the installed tree, which is Phase 2.

Consequences of the placement: a resolved dep's parent is the `mcp_server` node, so
`scope_of` returns `agent-dependency` (an agent component is in its lineage) with
**no change to the scope rule**, and `attribution_for` (nearest plugin ancestor)
still works. The resolver is **mode-agnostic — no repo/endpoint gate**: it
attributes to a specific MCP node via its launch, so there is no risk of
mislabeling host-level deps (the reason a blunt "all root deps" rule would have
needed a gate). Strategies 1–2 resolve whatever is statically present in either
mode; in repo mode that is repo source, in endpoint mode it is on-disk paths.

### Out of scope — Phase 2 (separate ADR)

**On-disk package-manager cache resolution** for the 45 external-`npx`/`uvx`
launches: read the real installed closure from `~/.npm/_npx/<hash>/package-lock.json`
(npx writes a full lockfile per invocation; parseable with the existing
`package_lock_json` parser) and `~/.local/share/uv/tools/<name>/` (uv tool venv:
`site-packages/*.dist-info`, `uv-receipt.toml`). This is the high-value path in
**endpoint** mode, where host MCP servers are predominantly external `npx`/`uvx`
published packages. It buys two things: (a) the launched package's **transitive
closure**, and (b) a **concrete resolved version** for the launched package itself —
turning today's unpinned `@latest` (mutable-install posture flag, no precise OSV
version match) into a pinned coordinate OSV can range-match. It is deferred because
it depends on host cache *state* (the server must have been run), heuristic
declaration→cache mapping, cache-layout drift across `npm`/`uv` versions, and a new
host read surface — distinct reliability characteristics from this deterministic
repo resolver. The Phase 2 ADR will also record that the mutable-install posture
finding still fires (the launch is genuinely unpinned regardless of what is cached),
and that the cached version is a last-resolved snapshot. **Resolution method for
Phase 2 is on-disk cache parsing, not registry/`deps.dev` lookup.**

## Alternatives considered

- **Blunt repo-level rule** ("repo ships any agent component → all its root deps are
  `agent-dependency`"). Rejected: over-counts — it would attribute the 45
  external-wrapper repos' unrelated root tooling as agent supply chain, needs a
  repo-mode gate to avoid mislabeling host-level deps in endpoint mode, and
  attributes to "the repo" rather than the specific component. Declaration-following
  is strictly more correct and needs no gate.
- **Recursive bare-dependency walk** (emit every `package.json`/`pyproject.toml` in
  the tree). Rejected: pulls in docs sites, `examples/`, test fixtures, vendored
  code as if they were the agent's deps. The non-recursive root walk is deliberate;
  the right way to reach a component's out-of-directory deps is to follow its
  declaration, not to walk every directory.
- **Amend the lineage scope rule (ADR-0037 #7).** Unnecessary: once `mcp_server`
  descends into its resolved deps, lineage already yields `agent-dependency`. No
  scope-semantics change is needed, so none is made.
- **Registry / `deps.dev` resolution for external launches.** Considered for Phase 2
  and deferred in favor of on-disk cache parsing: the cache reflects the *actual
  installed state* on the host (the real answer to "what deps does this server
  bring"), whereas a registry lookup of `@latest` is host-independent but adds a
  network/third-party dependency and may not match what is actually installed.
- **Do nothing (accept the leaf).** Rejected: `DesktopCommanderMCP` is a real,
  popular MCP server whose entire supply chain silently disappears; the pattern
  recurs for any repo that publishes the package its MCP server launches.

## Consequences

- ~18 repo-mode MCP servers (self-match + local-path, of 108 in the sample corpus)
  surface their dependency closure as `agent-dependency`; expect advisory counts on
  those repos to rise. The 45 external-`npx` and 30 remote correctly gain nothing in
  Phase 1 (external-`npx` closes in Phase 2).
- `descend()` gains an `mcp_server` branch; the build acquires a per-scan index of
  local manifest `name` → directory for strategy 1. The launch-parsing is reused
  from `tools/identity`, not written fresh.
- No change to `scope_of`, the filter, the BOM scope labels, or endpoint behavior
  beyond MCP nodes gaining resolvable children.
- Residual limitation (documented, not fixed): a component that *uses* root deps
  without *declaring* the linkage (e.g. a skill whose scripts import root packages)
  is still not attributed — there is no static declaration to follow. The blunt
  fallback that would catch it is rejected above for over-counting.

## When to revisit

- **Phase 2:** on-disk cache resolution for external `npx`/`uvx` launches (its own
  ADR + plan), the high-value endpoint path and the concrete-version OSV-match win.
- **Hooks / script-commands** are launchers too (`command` running a local
  script/named package) and are leaves today; the same resolver extends to them
  when their dep coverage is needed.
- If a root-level MCP manifest format (e.g. `server.json`) is parsed and makes some
  repos expose a root MCP component directly, its deps attribute by containment and
  the launch resolver becomes redundant for those (harmless overlap).
