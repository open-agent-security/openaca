# First-Run Inventory Output Implementation Plan

**Goal:** Make the default text scan output product-shaped — inventory-first —
so a first run reads as "OpenACA understands my agent stack" instead of "0 CVEs
found." Today the inventory tree only renders to **stderr** under `-v`; the
default stdout output is findings-centric and a clean scan reads as if the tool
did nothing.

**Architecture:** The inventory tree renderers already exist
(`render_inventory_tree`, `render_repo_inventory_tree` in `tools/render.py`) and
already take `(refs, findings)`. This plan moves the tree into the **default
stdout** path by restructuring `render_text` into a sectioned card —
Target → Inventory → Findings → Posture → Summary → Next — and threading a
pre-rendered tree string + target descriptor + next-action hints from each scan
command into `_emit` → `render_text`. `-v` stays diagnostics-only (overlay
counts, OSV federation targets, parser warnings, raw match lines — all already
on stderr).

This is a **product-surface change, not an architecture change.**
`ComponentRef[]` (+ `attributed_to`) remains the composition IR; the inventory
tree and the AgentBOM stay sibling views of it. We explicitly do **not**
introduce a `ScanReport`/`CompositionGraph` model or route terminal rendering
through CycloneDX — that larger refactor waits until a second scan surface
(Claude Desktop, external-analyzer adapters) lands. See the "Deferred" section.

**Renderer stays pure.** `render_text` is a pure `(inputs) -> str` function and
gets golden/snapshot tests. The caller (each scan command) assembles the tree
string and target descriptor; the renderer only lays them out.

## Design decisions (settled; encode as-is)

- **Clean-scan must feel useful.** Even with zero findings, default output shows
  Target + Inventory + a Summary line (`scanned N, M · advisories: 0 ·
  posture: skipped`). The existing "general SCA scanner" framing footer folds
  into the Summary section rather than being the whole output.
- **Golden tests normalize for stability.** Snapshots are taken with
  `use_color=False` (no ANSI) and any absolute paths relativized, so fixtures
  don't break on machine/path/color differences.
- **`render_text` is compositional, not dual-mode.** It gains keyword-only
  optional params (`target`, `inventory_tree`, `next_actions`). Each maps to an
  optional section: when an arg is absent that section is omitted; when all
  three are absent `render_text` emits just the findings + posture + summary
  body it produces today. This is *not* back-compat hedging — the sections are
  genuinely optional, and the only callers that omit them are the ~17
  `test_render.py` unit tests that exercise findings-formatting in isolation.
  The CLI scan paths **always** pass all three, so real first-run output is
  always the full card. (Verified: the only non-test caller of `render_text` is
  `scan.py`.)
- **Section order is fixed:** Target → Inventory → Findings → Posture →
  Summary → Next. Findings and Posture sections keep their current internal
  formatting (IDs already render prominently — preserve that).

## Target + Next-actions content per mode

`repo`:
- Target: `host surface: repository`, `path: <target>`
- Next: `emit Agent BOM: openaca bom repo --target <target> --output openaca-bom.json`
  (the `bom repo` command takes `--target`, not a positional — see
  `tools/bom_cli.py`)

`endpoint`:
- Target: `host surface: Claude Code`, `config: <config_dir>`,
  `project: <project | not included>`
- Next: `include project-local config: openaca scan endpoint --project .`
  (only when `project is None`); `emit Agent BOM: openaca bom endpoint
  --output openaca-bom.json`; `upload to Fleet: openaca fleet collect endpoint`

`bom`:
- Target: `source: Agent BOM`, `file: <input_path>`,
  `original target: <target_type> <target>` (when present in the BOM)
- Next: (none required; omit the section if empty)

## Tasks

- [ ] **Characterize current output first.** Add golden tests in
  `tests/test_render.py` that snapshot the *current* `render_text` output for
  three cases — clean scan (no findings), findings present, findings + posture
  — using fixed `findings`/`advisory_index`/`stats` inputs and `use_color=False`.
  Commit these as the baseline so the restructure diff is observable and
  intentional. Add a small helper that strips ANSI and relativizes paths for
  snapshot comparison.

- [ ] **Add a `RenderTarget` dataclass** to `tools/render.py` (alongside
  `ScanStats`): optional fields `host_surface: str | None`, plus an ordered
  `list[tuple[str, str]]` of label/value rows (e.g. `("config", "~/.claude")`)
  so each mode supplies its own rows without the renderer hard-coding modes.
  Pure data; no behavior.

- [ ] **Extend `render_text` signature** with keyword-only optional params:
  `target: RenderTarget | None = None`, `inventory_tree: str | None = None`,
  `next_actions: list[str] | None = None`. Existing positional args unchanged.
  Add a unit test asserting that with all three new args absent, `render_text`
  still produces the prior findings/posture/summary body verbatim (the
  `test_render.py` call sites rely on this — they test findings-formatting in
  isolation and never supply card inputs).

- [ ] **Restructure `render_text` into the sectioned card.** Emit, in order:
  a Target block (from `target`), an Inventory block (the `inventory_tree`
  string verbatim, or `(no components detected)` when empty/None), the existing
  Findings block (or the no-findings line), the existing Posture block, a
  Summary line (`scanned <unit_phrase>, <component_phrase> · advisories: <n> ·
  posture: <n|skipped>` + sources + parse-failure note), and a Next block from
  `next_actions` (omit if empty). When `target`/`inventory_tree`/`next_actions`
  are all absent those three sections are omitted and the findings + posture +
  summary body renders as today. The clean-scan path no longer early-returns
  before the Summary when card inputs are present. Add a *new* golden snapshot
  for the full card (all card inputs supplied, including a clean-scan card with
  zero findings); leave the legacy-body baselines from Task 1 intact as the
  no-card-args case.

- [ ] **Build the inventory tree unconditionally and thread it through `_emit`.**
  In `repo`, `endpoint`, and `scan_bom`, build the tree string for text output
  regardless of `verbose` (repo: `render_repo_inventory_tree(target, grouped,
  findings, ...)`; endpoint: `render_inventory_tree(refs, findings, ...)`; bom:
  `_render_bom_inventory_tree(...)`). Add `target`, `inventory_tree`, and
  `next_actions` params to `_emit` and forward them to `render_text`. Only the
  text renderer consumes them; `json`/`github`/SARIF paths ignore them.

- [ ] **Demote the verbose stderr tree to avoid duplication.** Since the tree
  now renders to stdout for text format, remove the `verbose` stderr tree prints
  in `repo` (scan.py ~548-556) and `endpoint` (~683-690) and `scan_bom` for the
  **text** format. Keep them for non-text formats (json/github/sarif) where
  stdout is machine output and the stderr tree is still the only human view.
  Keep all other `-v` diagnostics (overlay count, federation targets, parser
  warnings, raw `matched N finding(s)` lines) exactly as-is on stderr.

- [ ] **Wire per-mode Target + Next-actions** in each command per the content
  table above. For the endpoint command, the always-on stderr
  `detected config_dir=..., project=...` preamble must **not** precede the
  polished stdout card: emit it only for non-text formats or under `-v`. For
  text format the Target block owns scan-scope transparency (so first-run
  output is the card, not a stderr line then a card). Likewise the endpoint
  `--project` reminder becomes the first `next_actions` entry when
  `project is None` and the stderr note is dropped for text format (kept for
  non-text/`-v`).

- [ ] **Integration assertions in `tests/test_scan.py`.** Assert default
  (non-verbose) text output for an endpoint scan against a fixture config and a
  repo scan against a fixture repo now contains: the Target block, plugin/MCP/
  skill tree lines, finding IDs when findings exist, and the Summary line. Use
  the existing fixtures under `tests/fixtures/`; add a minimal endpoint/repo
  fixture only if none produces a non-trivial tree.

- [ ] **(Optional, separate — do not block on this) Remove the BOM
  pass-through** in `repo` and `endpoint` only. `build_agent_bom(...)
  .component_refs()` returns the input refs unchanged (frozen `ComponentRef`,
  strict zip, no dedup/mutation) and discards the computed bom-refs/edges — it
  is removable overhead in these two paths. Replace with the filtered refs
  directly **only if** the existing test suite fully covers these paths and the
  removal touches no matcher/federation BOM side effect. The `scan_bom` path
  (scan.py ~791) legitimately needs the BOM — leave it. If removal forces any
  reasoning about BOM semantics, skip it and leave a one-line note for the later
  `ScanReport` refactor.

- [ ] **Update the README expected-output block.** `README.md` (~line 124) has
  an "Expected output:" block showing the old findings-first format
  (`Found 1 vulnerability in 1 package. ...`). Replace it with the new card
  shape for that same `openaca scan repo --target .` demo (Target → Inventory →
  Findings → Summary → Next), matching the new golden snapshot byte-for-byte so
  docs and behavior can't drift. Note: the external `openaca-demo` repo
  referenced just below is out of scope here (separate repo) — only the
  in-README block is updated.

- [ ] **Run the full gate:** `uv run ruff format`, `uv run ruff check`,
  `uv run pyright`, `uv run pytest`, and `uv run openaca lint` (if the change
  touches anything lint covers). All green before done.

## Verification

- Default `openaca scan endpoint` (no `-v`) on a fixture config prints the
  sectioned card to **stdout** with the inventory tree visible, and a clean
  scan still shows Target + Inventory + Summary (not just "no findings").
- `-v` adds only diagnostics on stderr (overlay count, federation targets,
  parser warnings, raw matches) — the tree is not duplicated to stderr for text
  format.
- Non-verbose machine-format stdout (`--format json`, `--format github`, SARIF)
  is byte-for-byte unchanged (golden/existing tests confirm). Verbose stderr for
  those formats stays diagnostic-only and may differ from text-format behavior
  by design (e.g. the stderr tree is retained for machine formats, dropped for
  text).
- Finding and posture IDs (`GHSA-*`, `CVE-*`, `MAL-*`, posture rule IDs) render
  prominently in the Findings/Posture sections.
- Golden snapshots for `render_text` are stable across machines (ANSI stripped,
  paths relativized).

## Deferred (explicitly out of scope for this plan)

- `ScanReport` extraction / pure-renderer dispatch refactor. Justified only when
  a second scan surface or external-analyzer adapter lands; doing it now is
  speculative structure.
- `CompositionGraph` IR as an explicit model. `ComponentRef[]` + `attributed_to`
  already serves as the IR; both the tree and the BOM project from it. Name and
  extract `CompositionGraph` during the `ScanReport` refactor, not before.
- Ingesting external analyzer findings (e.g. SkillSpector SARIF) as an
  observation/evidence layer. Separate future plan; this plan only reshapes
  OpenACA's own first-run output.
