# Plan 036 - Triage Command And Exposure Reports

> **For agentic workers:** Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared triage/report layer over scan evidence. `scan` continues
to collect facts; `triage` ranks and explains component-centric exposures. The
friendly path is `openaca scan endpoint --report exposure --output report.md`;
the composable path is `openaca scan endpoint --format json > scan.json`
followed by `openaca triage scan.json --report exposure --output report.md`.

**Architecture:** Introduce a pure triage module that consumes structured scan
output and returns triage cards. Renderers turn cards into text, Markdown, and
JSON. CLI plumbing exposes both `openaca triage` and `scan --report exposure`,
with both paths using the same engine. No new detection rules, runtime claims,
or Cloud-only behavior in this plan.

**Tech stack:** Python 3.11, Click, pytest, existing scan JSON and Agent BOM
composition data. Gate: `uv run ruff check .`, `uv run ruff format --check .`,
`uv run pyright`, and focused pytest.

**Design inputs:** ADR-0040 and `docs/specs/triage-reports.md`.

---

## Task 1: Scan artifact contract for triage

**Files:**
- Modify: `tools/scan.py` or existing scan-result module
- Modify: `tools/render.py` only if needed to expose existing structured data
- Test: `tests/test_scan.py`

- [ ] **Step 1 - failing test.** Add a test that runs `openaca scan endpoint
  --format json` on a fixture with a plugin -> MCP -> vulnerable package path
  and asserts the JSON contains enough data for triage: finding type, component
  label, matched advisory, normalized severity, fix text when available,
  `component_path`, `declared_by`, and BOM/component identity.
- [ ] **Step 2 - implement missing fields only.** Do not redesign scan JSON.
  Add the smallest missing fields needed by the triage engine. If all fields
  already exist, make the test document the contract and leave production code
  unchanged.
- [ ] **Step 3 - verify.** Run the focused scan JSON test.

## Task 2: Pure triage card model

**Files:**
- Create: `tools/triage.py`
- Test: `tests/test_triage.py`

- [ ] **Step 1 - failing tests.** Cover:
  - one vulnerability finding becomes one card;
  - multiple findings under the same plugin/MCP/skill group into one component
    card;
  - component cards retain evidence references;
  - cards carry one action from `remove`, `pin`, `upgrade`, `approve`,
    `replace`, `accept`, or `review`;
  - ranking is deterministic when severity ties.
- [ ] **Step 2 - implement dataclasses.** Add `TriageCard`, `TriageEvidence`,
  and `TriageAction` with plain typed fields.
- [ ] **Step 3 - implement grouping.** Group by the highest useful agent
  component in the finding path, falling back to the finding component when no
  agent ancestor exists.
- [ ] **Step 4 - implement conservative ranking.** Start with normalized
  severity, then active/agent lineage, posture weight, confidence, and stable
  component label as tie-breakers. Do not use capability facts unless present
  in scan evidence.
- [ ] **Step 5 - verify.** Run `uv run pytest tests/test_triage.py -q`.

## Task 3: Action and explanation rules

**Files:**
- Modify: `tools/triage.py`
- Test: `tests/test_triage.py`

- [ ] **Step 1 - failing tests for action mapping.**
  - vulnerable dependency with fixed version -> `upgrade`;
  - mutable install posture -> `pin`;
  - insecure remote transport -> `replace` or `review`;
  - unknown/low-confidence observation -> `review`;
  - clean-but-unapproved component, if represented in scan evidence -> `approve`.
- [ ] **Step 2 - implement explanation templates.** `why_it_matters` must use
  only evidence in the scan artifact. Include provenance labels such as
  advisory-derived, scanner-derived, or external-scanner-derived.
- [ ] **Step 3 - add scope caveats.** Cards and reports should be able to say
  when runtime behavior, remote MCP internals, or project context were not
  inspected.
- [ ] **Step 4 - verify.** Run focused triage tests.

## Task 4: Triage renderers

**Files:**
- Create: `tools/triage_render.py` or extend the existing renderer if simpler
- Test: `tests/test_triage_render.py`

- [ ] **Step 1 - failing text renderer test.** Assert text output is concise,
  component-centric, and includes priority, component label, composition path,
  evidence summary, action, confidence, and scope caveats.
- [ ] **Step 2 - failing Markdown renderer test.** Assert Markdown output has:
  target/scope summary, counts, top five cards, "What we could not see", and
  suggested next step.
- [ ] **Step 3 - failing JSON renderer test.** Assert JSON output preserves
  cards without losing evidence references.
- [ ] **Step 4 - implement renderers.** Keep Markdown report human-readable and
  deterministic. Do not include raw source snippets.
- [ ] **Step 5 - verify.** Run renderer tests.

## Task 5: `openaca triage` CLI

**Files:**
- Modify: CLI entrypoint module
- Test: `tests/test_scan.py` or a new CLI-focused test file matching repo
  convention
- Docs: `docs/reference/cli.md`

- [ ] **Step 1 - failing CLI tests.**
  - `openaca triage scan.json --report exposure` emits text by default;
  - `--output report.md --format markdown` writes a Markdown file;
  - `--format json` emits machine-readable cards;
  - malformed scan JSON exits non-zero with a clear error.
- [ ] **Step 2 - implement command.** `triage` reads a scan JSON artifact and
  invokes the pure triage engine. It does not read repos/endpoints and does not
  query OSV.
- [ ] **Step 3 - document command.** Update CLI reference with the scan/triage
  split and examples.
- [ ] **Step 4 - verify.** Run CLI tests.

## Task 6: `scan --report exposure` shortcut

**Files:**
- Modify: scan CLI command plumbing
- Test: `tests/test_scan.py`
- Docs: README and `docs/reference/cli.md`

- [ ] **Step 1 - failing tests.**
  - `openaca scan endpoint --report exposure` renders a report from the same
    triage engine as `openaca triage`;
  - `--output report.md` writes the report and keeps scan exit behavior
    findings-driven;
  - report generation works with `--include-posture`;
  - unsupported combinations fail clearly.
- [ ] **Step 2 - implement shortcut.** Internally run the normal scan path,
  retain the structured scan artifact in memory, pass it to triage, and render
  the requested report.
- [ ] **Step 3 - avoid duplicate logic.** The shortcut must not maintain a
  separate report renderer or ranking path.
- [ ] **Step 4 - verify.** Run focused scan CLI tests.

## Task 7: Claude Code plugin contract

**Files:**
- Modify: docs only in this repo, unless the plugin integration docs live here
- Coordinate with: `openaca-claude-plugin`

- [ ] **Step 1 - document invocation.** The plugin should call
  `openaca scan endpoint --report exposure --output <path>` or the two-step
  `scan`/`triage` flow. It should not implement report logic itself.
- [ ] **Step 2 - document latency behavior.** Report mode should avoid optional
  slow external scanners unless the user opts in, matching plugin UX needs.
- [ ] **Step 3 - file follow-up in plugin repo** if plugin command work is not
  done in the same branch.

## Task 8: Final docs and verification

**Files:**
- Modify: README
- Modify: `docs/reference/cli.md`
- Modify: release notes when implementation ships

- [ ] **Step 1 - README quickstart note.** Add a short local report example
  after endpoint scan docs.
- [ ] **Step 2 - scope honesty.** Docs must state that exposure reports are
  static composition reports, not runtime monitors or exploit proofs.
- [ ] **Step 3 - full verification.** Run:
  - `uv run ruff check .`
  - `uv run ruff format --check .`
  - `uv run pyright`
  - focused pytest for triage/scan/rendering
- [ ] **Step 4 - commit, push, and open a ready PR.**
