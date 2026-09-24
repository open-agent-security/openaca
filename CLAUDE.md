# OpenACA — Project Conventions for Claude

OpenACA (Agent Composition Analysis) is an open-source, OSV-compatible
agent-context overlay layer for AI agent infrastructure: plugins, MCP servers,
skills, agent frameworks, model proxies, and runtime components. OpenACA does
not mint vulnerability IDs; overlays sit on top of upstream OSV records
(GHSA / CVE / OSV / PYSEC / MAL).

## Project scope: OSS only

This repository contains the open-source overlay corpus, schema, parsers, and
reference scanner. **All project artifacts in this repo are OSS-focused.**

When writing or editing any file in this repo (specs, plans, READMEs, ADRs,
overlays, code, comments, commit messages, PR descriptions), do **not** include:

- Commercial product plans, monetization strategies, pricing, paid-tier features.
- Vendor comparisons or competitive positioning (e.g., "better than X," "unlike Y").
- Market analysis, go-to-market narratives, sales framing.
- Vendor names framed as *competitors*. Naming a tool we *use* is fine; naming a
  product as a competitor is not.

If a draft contains content in these categories, rewrite to remove. When uncertain
whether something falls in scope, prefer to omit and flag for human review.

OpenACA's authority depends on positioning as a neutral, vendor-agnostic public
overlay layer. Commercial or competitive framing in OSS artifacts erodes that.

### What is in scope

- Operational decisions that reference external tools by name where attribution
  is required (e.g., "match detected during OpenACA triage using
  `cisco-ai-defense/mcp-scanner` v0.X").
- Technical interoperability (aliasing CVE/GHSA records, adopting OWASP Agentic
  Top 10 categories, using the OSV schema).
- Cross-project collaboration notes (engaging OSV.dev, MCP TSC, OpenSSF, etc.).

The test: is this content describing *what OpenACA does or how it interoperates*, or
is it positioning OpenACA *against* something? The first is fine; the second is out.

## Common commands

```bash
uv sync --frozen                      # install / update deps
bash scripts/install-hooks.sh         # one-time, install the pre-push gate
uv run ruff check .                   # lint
uv run ruff format --check .          # format
uv run pyright                        # types
uv run pytest -q                      # tests
```

## Before pushing a PR

`scripts/git-hooks/pre-push` runs the four gates above and refuses to push on a
dirty worktree. Bypass with `git push --no-verify` only when local test
infrastructure is broken and you are pushing the fix; that allowance is for a
person at a keyboard, never for an automated fixer.

## Repository layout

- `docs/specs/openaca-thesis.md` — what OpenACA is, the V0 → V1 roadmap.
- `docs/plans/NNN-<topic>.md` — one implementation plan per V0 deliverable.
- `docs/adrs/NNNN-<topic>.md` — durable architecture decisions.
- `schema/openaca.schema.json` — canonical overlay schema.
- `overlays/<ID>.yaml` — bundled OpenACA overlays (upstream IDs; OpenACA agent-context metadata).
- `tools/` — linter, scanner, static export, render, and overlay helpers.
- `action.yml` — reference GitHub Action at repo root.
- `CONTRIBUTING.md` — contributor flow, overlay authoring guide.

## Conventions

### Authoring

- Default to writing no comments. Add one only when the *why* is non-obvious.
- Match existing file style; surgical edits only. Don't refactor adjacent code
  unless the task requires it.
- Every overlay ships with reproducible evidence where possible (vulnerable
  config snippet, malicious tool description, affected command). Treat fixtures
  as overlay metadata, not a separate corpus.

### Schema and IDs

- V0 overlays use upstream IDs (`GHSA-*`, `CVE-*`, `OSV-*`, `PYSEC-*`, `MAL-*`).
  OpenACA does not mint its own IDs. See ADR-0009.
- Overlay files live under `overlays/` named `<upstream-id>.yaml`.
- Canonical record shape (V0): `id`, `schema_version`, `modified`, and the
  `database_specific.openaca` block. `database_specific.openaca` carries
  `taxonomies{}`, `evidence_level`, and (for MAL-* records) `threat_kind`.
  `type: exposure` and `type: config` are reserved in schema but **rejected in
  V0 PRs** pending methodology docs.
- Severity, affected ranges, fix versions, references, and CVSS vectors
  come from the upstream OSV record. The scanner queries OSV.dev at scan
  time and merges the overlay into the returned record.
- Category: `owasp_agentic_top10[]` array referencing ASI01–ASI10, plus the
  other taxonomy families enumerated in the schema.
- No custom severity enum (no `agent_blast_radius` or similar parallel taxonomy).

### Linter discipline (CI)

- **Hard fail**: schema validation, ID format/uniqueness, required fields,
  CVSS parses, ASI category validity.
- **Warning / scheduled job (don't block PRs)**: link liveness, OSV/GHSA
  enrichment, remote alias resolution. External APIs are flaky; PRs shouldn't
  fail because of them.

### Overlay policy

- Overlays are keyed by an upstream record ID. OpenACA adds agent-context
  metadata; upstream sources own identity, affected ranges, severity, fixes.
- Where an agent-component ecosystem isn't yet served by an upstream pipeline
  (some marketplace flows, some MCP server identities), OpenACA's contributors
  pursue upstream disclosure first; the overlay lands once an upstream record
  exists.

## V0 scope (read `docs/specs/openaca-thesis.md` and `docs/adrs/0009-overlay-only-v0.md` for detail)

V0 ships:

1. Overlay-only schema (`database_specific.openaca`: taxonomies, evidence level,
   threat_kind on MAL records); `type: exposure` and `type: config` reserved
   but rejected.
2. Manifest parsers for `package.json`, `mcp.json`, `.claude-plugin/plugin.json`,
   `.claude/settings.json`. Cursor manifests are in scope as of plan 042
   (`cursor` is a registered agent kind) and Codex manifests as of plan 043
   (`codex`, including TOML `config.toml` and `agents/*.toml`), and Pi
   manifests/resources as of plan 047 (`pi`, including packages, extensions,
   skills, prompts and themes; native MCP adapters are not covered). Windsurf
   manifests remain V1.
3. 5+ bundled OpenACA overlays (`overlays/*.yaml`) keyed on upstream OSV record
   IDs (GHSA / CVE / OSV / PYSEC / MAL), adding agent-context taxonomies and
   evidence level. Scans query OSV.dev and merge overlays into the returned
   records.
4. Linter + CI per discipline above.
5. Static export pipeline: `overlays/*.yaml → JSON → all.zip → modified_id.csv`.
6. Reference GitHub Action: `open-agent-security/openaca@v1` with `action.yml` at
   repo root.
7. Disclosure policy (`SECURITY.md`): coordinated-disclosure guidance (OpenSSF
   baseline + OpenACA-specific defaults). **V0 documents the policy; does not
   operate it at scale.**

V0 does **not** ship: HTTP API, benchmark harness, public detection-rule format,
OpenACA-namespace vulnerability IDs, active disclosure pipeline, `type: exposure`
or `type: config` records.


## End-to-end tests live in `tests/test_e2e.py`

Unit tests live next to the code under test. **Cross-layer tests
that exercise multiple modules together against the real corpus
(`overlays/` + `schema/` + the parser/exporter modules) belong in
`tests/test_e2e.py`.**

Why a single growing file: cross-layer tests are about the *product
promise* (does OpenACA actually detect a vulnerable agent component?),
not about any one module. A single file makes the suite trivial to
read, hard to lose, and naturally evolves as plans land.

When a plan adds a feature that crosses module boundaries, ask:
*what's the one-screen test that demonstrates this layer wiring up
correctly with what's already there?* Add it to `test_e2e.py`. Examples:

- Plan 005 (reference action) → action-invocation test: invoke the
  Action's CLI surface against a fixture repo, verify it finds the
  same overlay match the parser-only test finds.
- Plan 006 (disclosure policy) → doc-only, no addition.
- A future "overlay diff" feature → diff-output test exercising
  the linter + the diff renderer + the corpus together.

Don't move existing unit tests here. Don't put e2e tests in module
test files. The boundary is: *does this test fail if any one of
several modules regresses?*

## Weighing review findings against a stated bar

A spec may declare a **robustness bar** — what it aims to get right, and what
it defers. When one does, weigh findings against it rather than against
completeness, and say which side of the bar a finding falls on.

The bar exists because severity is not uniform and reviewers cannot infer it.
"A surface an ordinary installation has goes uninventoried" and "a wrongly
typed scalar is coerced rather than rejected" are different problems: the first
breaks the product, the second is invisible unless the config is already
invalid. Without a stated bar both arrive as the same priority, and a review
can iterate indefinitely over a combinatorial tail while the important findings
are already fixed.

For a finding below the bar, record it as deferred with the cost of skipping it
— do not implement it, and do not re-raise it in the next round. For a finding
above it, fix it regardless of how narrow the trigger looks.

If a spec states no bar, review for correctness as usual. Adding one is the
spec author's job, not the reviewer's.

## Verifying claims about external behavior

When a review comment, design decision, or bug report turns on how a *third
party* behaves — an API contract (e.g. OSV.dev query semantics), a file or
lockfile format (e.g. `bun.lock`, `package-lock.json`), or a tool's actual
output — verify against ground truth before accepting or rejecting it:

- **Fetch the authoritative source.** Use `WebFetch` for the docs/spec,
  `WebSearch` to find it, or run a script (`uv run python`, `curl`) to hit the
  API or parse a real sample.
- **In-repo ADRs, plans, and tests are NOT evidence for an external claim.**
  They record what *we chose*, not what the third party actually requires. A
  test written by the same author who holds an assumption is self-referential:
  it confirms the assumption rather than falsifying it. (Both PR #97 and #98
  stalled on exactly this — fixtures/ADRs cited as proof of an external format
  or API contract they only asserted.)
- **Distinguish "I verified this is false" from "I could not disprove it."**
  Absence of in-repo disproof is not disproof. If you lack the access to
  verify (no network, no real sample, a denied tool), say so explicitly and
  **defer** — flag the uncertainty and escalate to a human reviewer rather
  than confidently pushing back on a bare citation. A factual dispute about
  external behavior that you cannot settle is a stop-and-ask, not a
  win-the-argument.

This applies to every agent — the interactive assistant, the `@claude` review
bot, and any subagent — not just one surface.

## Risky / hard-to-reverse actions

Carefully consider reversibility and blast radius. Local + reversible
(file edits, running tests) — fine to do directly. Hard-to-reverse,
shared-state, or visible-to-others — confirm first:

- Destructive: `rm -rf`, dropping tables, killing processes,
  overwriting uncommitted changes, force-deleting branches.
- Hard-to-reverse: force-pushing, `git reset --hard`, amending
  published commits, removing/downgrading dependencies.
- Visible to others: pushing code, creating/closing/commenting on PRs
  or issues, sending messages (Slack, email), modifying shared
  infrastructure or permissions.
- Uploading to third-party tools (diagram renderers, pastebins,
  gists) — the content gets indexed/cached even if later deleted.

After making requested repo changes, commit them, push the branch, and open a
ready PR by default. Do not leave finished work uncommitted or only local unless
the user explicitly asks. Use a draft PR only when the user asks for draft status
or the work is intentionally incomplete / blocked and needs review before it can
be ready. If the request did not clearly include publishing the work, confirm
before pushing. Even when pushing is authorized, confirm before opening a PR if
the user did not explicitly ask for one.

When you encounter an obstacle, don't use destructive actions as a
shortcut to make it go away. Identify the root cause; fix the
underlying issue rather than bypassing safety checks (e.g.,
`--no-verify`).

If you discover unexpected state — unfamiliar files, branches,
configuration — investigate before deleting or overwriting. It may
represent the user's in-progress work.

## Code Review Rules

### Reviewer

- Review the full PR against its base branch. Report all qualifying
  findings together; do not deliberately reserve findings for later rounds.
- Report concrete, actionable defects with supported failure scenarios.
  Respect explicit scope decisions and accepted tradeoffs. Do not present
  speculative hardening or optional improvements as correctness defects.
- Calibrate priority by impact and urgency:
  - P0: critical, broadly applicable failure requiring immediate action.
  - P1: serious defect that should be fixed before this change lands.
  - P2: normal-priority defect eligible for automatic fixing.
  - P3: low-priority suggestion.
  Do not inflate priority to make a finding eligible for automatic fixing.
- On subsequent reviews, verify earlier fixes and inspect their effects on
  callers and dependencies. Older code within the PR remains reviewable.
- When review history supports it, identify a finding as:
  - Regression: introduced since the previous reviewed head.
  - Late discovery: present at a previously reviewed head but not reported.
  - Unresolved: a previously reported defect remains.
  If the history is unavailable or ambiguous, say so rather than guessing.
- Deduplicate by underlying defect and remedy, not by title. Refer to an
  existing thread for an unresolved defect instead of opening another one.

### Automated Fix Rules

- Automatically address actionable CI failures and verified P0, P1,
  and P2 findings, whether raised by an automated reviewer or a human.
- P3 findings require explicit human approval before fixing. Maintain one
  updated summary with links to their threads. Do not mark them resolved
  merely because they are deferred.
- Validate each finding against the current head and relevant contracts.
  If its reasoning or priority is wrong, explain why rather than applying
  it solely because a reviewer requested it. Surface unresolved disputes
  for human judgment.
- Fix the underlying invariant across relevant call sites. Test the
  failure class rather than only the reported example.
- Preserve accepted fixes and regression tests when reviewing a rebased PR or
  when replacing or simplifying its implementation. Remove a test only when
  the behavior it protects is intentionally changed or removed, and explain
  that decision.
- Batch related fixes into one tested update before requesting re-review.
  Avoid duplicate review requests for the same head.
- An automated fixer pushes ordinary commits to the existing PR branch and
  runs every required gate. It does not bypass gates, merge, rebase, or
  force-push, or change workflows, permissions, credentials, or branch
  protection. Report an environment failure or required rebase as a blocker.
- After pushing fixes, wait for CI and a completed review of the current
  head. Silence, an older review, or a running review is not clearance.
- Stop and ask for human input when the same failure repeats without progress.
- Stop the automatic fix cycle when CI passes, the current head has been
  reviewed, and no actionable P0/P1/P2 findings remain. A reviewer
  thumbs-up is not required.
- If only P3 findings remain, report:
  "Automatic fixes complete for <SHA>; CI passed. P3 suggestions await
  author approval."
  Do not claim the PR has no findings or has been approved.
- Count automated fix rounds since the most recent human-authored corrective
  commit, and stop when the count reaches seven. A round is a fix pushed as a
  new head for review; failed local validation does not count. A human-authored
  commit is corrective when it materially addresses the reported blockers; it
  resets the count to zero whether it arrives before or after the cap. Merging,
  rebasing, or otherwise synchronizing the branch does not reset the count. If
  PR and session history are insufficient to determine the count, stop and ask
  the author rather than guessing.
- At the cap, remain subscribed but make no edits. Put a message in the PR:
  "Review cap limit reached. @<author> Please take a step back to review the
  design and push a corrective commit to reset the review cap." In the same
  comment, explain why review has not converged and suggest concrete
  simplifications or spec/ADR changes. Resume only after the reset defined
  above.

