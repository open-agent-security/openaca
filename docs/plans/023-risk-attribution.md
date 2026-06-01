# Risk Attribution (Containment-Aware Findings) Implementation Plan

**Goal:** Findings explain not just *what* is vulnerable but *how it entered
the agent stack*. When a bundled component (MCP server, hook, dependency) is
vulnerable, attribute it up to the plugin/host that introduced it — so a user
sees "you installed plugin X; it's exposed because it bundles vulnerable
component Y," not just an isolated leaf advisory.

**Architecture:** Attribution already exists in the data — `ComponentRef.
attributed_to` links a bundled component to its parent plugin, and the
inventory tree already groups children under their plugin. The gap is purely in
*surfacing*: the plugin-header finding marker is computed only from advisories
against the plugin itself (`render.py` `_build_plugin_node`, both the endpoint
and repo variants), so a vulnerable bundled child never flags its parent. This
plan propagates descendant findings up to the parent header (visually
distinguished from a direct hit) and adds an explicit introduction-path line to
the text card's Findings section and to SARIF.

**Scope discipline:** This is presentation/attribution over the existing
composition graph — *not* new finding records and *not* a new data model. The
finding stays on the leaf component; the parent is flagged as a *containment*
relationship, not given its own advisory. No `ScanReport`/`CompositionGraph`
refactor (still deferred). V1 attributes to the **immediate parent** the
`attributed_to` chain provides; deeper multi-level path rendering
(host → plugin → MCP → dep) is deferred unless the attribution chain already
carries that depth (confirm in Task 1).

**Tech stack:** Python (`tools/render.py`, `tools/sarif.py`), vitest-style
golden tests in `tests/test_render.py`, e2e in `tests/test_e2e.py`.

## Design decisions (encode as-is)

- **Direct hit vs. bundled hit are visually distinct.** A direct advisory on
  the plugin renders the existing `[! GHSA-…]` marker. A descendant advisory
  renders a *different* marker on the plugin header — `[! bundles: GHSA-…]` —
  so users can tell "the plugin itself is vulnerable" from "the plugin pulls in
  something vulnerable." Both can appear.
- **Attribution is presentation, not new findings.** Finding counts are
  unchanged; `len(findings)` and the Summary's `advisories: N` still count leaf
  findings. Propagation only adds markers + path context. (This is the
  alternative a reviewer is most likely to challenge — "why not emit a finding
  for the plugin too?" — answer: it would double-count and conflate "is
  vulnerable" with "contains vulnerable.")
- **Path depth is honest to the data.** Render the introduction path to
  whatever depth `attributed_to` resolves. If it's single-level today
  (component → plugin), the path is `<plugin> → <component>`; deeper chains are
  a follow-up, not a V1 claim.

## Tasks

- [x] **Confirm attribution depth.** **Finding:** `ComponentRef.attributed_to`
  is a single string (one parent), set by `tools/parsers/*` and mirrored onto
  `Finding.attributed_to` in `tools/matcher.py`. Bundled MCPs/skills/hooks *and*
  tier-2 deps are all attributed directly to the plugin identity (no
  MCP→dep intermediate link). So attribution is **single-level**: the
  introduction path is `<plugin> → <component>`. Deeper chains
  (`plugin → MCP → dep`) need a parent-chain model — deferred to the
  `CompositionGraph` work. V1 path rendering is 2-level.

- [x] **Add a containment marker helper (TDD).** In `tools/render.py`, add
  `_containment_marker(ids, use_color)` rendering `  [! bundles: <ids>]`
  (red when colored), parallel to the existing `_finding_marker`. Unit-test in
  `tests/test_render.py`: empty ids → `""`; ids → sorted deduped
  `[! bundles: GHSA-A, GHSA-B]`; colored wraps in the red ANSI codes.

- [x] **Propagate descendant findings to the plugin header — endpoint tree
  (TDD).** In `_build_plugin_node` (`render.py` ~995), after computing the
  direct `marker`, collect advisory ids from all bundled category items and
  tier-2 dep refs (the same refs already iterated at ~1030 and ~1037), and
  append a `_containment_marker` to the header when that set is non-empty and
  the id is not already in the direct marker. Test: a plugin with a clean
  self-identity but a bundled MCP that matched a finding renders
  `… [! bundles: GHSA-…]` on the header AND the leaf keeps its own `[! GHSA-…]`.

- [x] **Propagate descendant findings to the plugin header — repo tree (TDD).**
  Apply the same change to the repo-tree `_build_plugin_node` variant
  (`render.py` ~1260, which marks the header at ~1274 from direct findings
  only). Test against the repo-tree builder with a plugin bundling a vulnerable
  dep.

- [ ] **Add the introduction-path line to the card Findings section (TDD).** In
  `_render_finding_groups` (`render.py`), where a group currently emits
  `via:      <attributed_to>`, add an explicit path line when attribution
  exists: `path:     <introduction path>` (e.g. `superpowers → @scope/foo`),
  built to the depth Task 1 confirmed. Keep the existing `fix:` line. Golden:
  regenerate `card-endpoint.txt` so the path line is locked; add a focused unit
  test asserting the path renders for an attributed finding and is omitted for a
  direct (unattributed) finding.

- [ ] **Surface attribution in SARIF (TDD).** In `tools/sarif.py` (which already
  emits `properties.attributed_to`), add the resolved introduction path as
  `properties.introduction_path` (string or ordered array). Unit-test that a
  finding with attribution emits the property and one without omits it. Machine
  consumers get the containment relationship without parsing text.

- [ ] **e2e: plugin flagged because a bundled component is vulnerable.** Add a
  test to `tests/test_e2e.py` using a fixture repo/endpoint where a plugin
  bundles a component that matches a bundled overlay (reuse/extend an existing
  fixture such as `exposed-mcp`). Assert default text output shows: the plugin
  header carries `[! bundles: …]`, the leaf carries its own `[! …]`, and the
  Findings section shows the `path:` line. This is the one-screen demonstration
  of the differentiator wiring up end to end.

- [ ] **Promote the README to four capability bullets.** After PR #94
  (`docs/readme-positioning`) merges, rebase this branch on main and add the
  fourth bullet to `## What OpenACA does`:
  `**Risk Attribution** — when a component is vulnerable, trace it back through
  the graph to the plugin, skill, or MCP server that introduced it.` Place it
  between Composition Graph and Advisory Intelligence (the "what is it → what's
  in my stack → how did the risk get here → what evidence says it's risky"
  ladder). If #94 is not yet merged when the rest of this plan is done, do this
  task last and rebase.

- [ ] **Run the full gate:** `uv run ruff format`, `uv run ruff check`,
  `uv run pyright`, `uv run pytest`, `uv run openaca lint`. All green.

## Verification

- A default `openaca scan endpoint`/`repo` on a fixture where a plugin bundles a
  vulnerable component shows the plugin header flagged `[! bundles: <id>]`,
  distinct from a direct `[! <id>]`, and the leaf still flagged.
- The Findings section shows a `path:` line tracing the introduction route.
- SARIF carries `properties.introduction_path`.
- Finding counts and the Summary `advisories: N` are unchanged (propagation
  added markers/paths, not findings).
- Golden snapshots updated and stable; full gate green.

## Deferred

- Multi-level path chains beyond what `attributed_to` carries today (if Task 1
  finds attribution is single-level).
- `ScanReport`/`CompositionGraph` extraction — still gated on a second scan
  surface.
- Downward "blast radius" view (given a vulnerable component, list every plugin/
  host that bundles it) — the inverse traversal; a separate feature.
- Posture-finding attribution (this plan covers advisory findings; posture
  propagation can follow the same pattern later).
