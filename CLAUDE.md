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
- Specs are living documents in `docs/specs/<topic>.md`. A feature that
  changes a documented design updates that spec in the same PR.

## V0 scope (read `docs/specs/openaca-thesis.md` and `docs/adrs/0009-overlay-only-v0.md` for detail)

V0 ships:

1. Overlay-only schema (`database_specific.openaca`: taxonomies, evidence level,
   threat_kind on MAL records); `type: exposure` and `type: config` reserved
   but rejected.
2. Manifest parsers for `package.json`, `mcp.json`, `.claude-plugin/plugin.json`,
   `.claude/settings.json`. Cursor manifests are in scope as of plan 042
   (`cursor` is a registered agent kind) and Codex manifests as of plan 043
   (`codex`, including TOML `config.toml` and `agents/*.toml`); Windsurf
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

## Writing documents

Write all Markdown documents in ASD-STE100 Simplified Technical English.
This includes specs, ADRs, READMEs, plans and release notes. The rule
applies to every author, human or agent.

- Write one idea in each sentence.
- Use a maximum of 20 words in an instruction and 25 words in a
  description.
- Use a maximum of six sentences in a paragraph.
- Use the active voice and the present tense.
- Start an instruction with a verb, for example "Run the tests."
- Use "a", "an" and "the". Do not remove them to make the text shorter.
- Use one term for one thing, and use it the same way in all of the
  document.
- Use simple, common words. Do not use filler, such as "in order to" or
  "utilize".
- Do not use em-dash asides. Write a new sentence.
- Do not change technical names, commands or code. Code blocks, tables and
  quoted text do not have to obey these rules.

Apply these rules to the text that you write or change. Do not rewrite an
existing document only to change its style. Do not edit an accepted ADR to
change its style.

These rules are for authors. They are not a code review topic. Reviewers
do not report style findings.

## Architecture Decision Records (ADRs)

**Amend or supersede: one test.** Ask if any numbered decision in the old
ADR still binds. If one does, amend: put `amends: NNNN` on the new ADR and
`amended-by: NNNN` on the old one, and both stay active. If none does,
supersede. Only this test decides. The size of the change, the importance
of the replaced clause, and the old ADR's "When to revisit" text do not
change the result. A reviewer reports the choice only when the finding
names every numbered decision in the old ADR and shows that none still
binds.

## Specs and ADRs

A spec holds the design. An ADR holds one decision and its rejected
alternatives.

- **Spec** (`docs/specs/`): what the system does and how. It has the
  contracts, the data model, the flows, the commands, the failure
  behavior and the verification. An agent can implement from the spec
  alone. Start a new spec from `docs/specs/TEMPLATE.md`.
- **ADR** (`docs/adrs/`): a choice where the rejected alternative is
  plausible. Keep the Decision section short. Write one paragraph, or a
  short numbered list of choices. Put mechanisms, flags and test plans
  in the spec.

**Write the spec first.** Write or update the spec. Then write ADRs for
the decisions that meet the ADR bar. List those ADRs in the "Decisions"
table of the spec. Link each ADR to the spec section that has the
detail. Do not write "This spec implements ADR-NNNN". If you write that
sentence, the design is in the wrong document.

**The project section of CLAUDE.md names the spec layout.** Use one of
these:

- Dated design specs, `docs/specs/YYYY-MM-DD-<topic>.md`, one for each
  design effort.
- Living component specs, `docs/specs/<component>.md`, updated in place.
  A feature that changes a component updates the spec of that component
  in the same PR.

**Not every change needs a spec or an ADR.** Bug fixes, cleanups,
wording changes and UI changes get no ADR. The ADR hooks at compaction
and at session end tell you to look for decisions. They do not tell you
to write an ADR.

## Code Review Rules

**The goal of review is to converge.** The reviewer and the fixer both work
toward a PR that can merge in few rounds. Each round costs a full CI run and
a review, and a finding on text that was already reviewed starts another
round. Converging does not mean letting a serious defect through. The first
review catches the glaring issues, and later reviews check only what changed.

### Reviewer

- **The first review of a PR is the complete one.** Review the full PR
  against its base branch. Report every qualifying finding together. Do not
  reserve findings for later rounds.
- **A later review covers only what changed since the previous reviewed
  head.** That is the PR's new commits, plus existing code whose behavior
  those commits change, such as a caller or dependency of changed code.
  Changes that a merge or rebase brings in from the base branch are not part
  of it.
- **Do not report a defect that existed at the previous reviewed head**,
  unless the new commits change its behavior. Exception: report a P0 or P1
  even if it existed before, and label it a late discovery.
- Report concrete, actionable defects with supported failure scenarios.
  Respect explicit scope decisions and accepted tradeoffs. Do not present
  speculative hardening or optional improvements as correctness defects.
- Calibrate priority by impact and urgency:
  - P0: critical, broadly applicable failure requiring immediate action.
  - P1: serious defect that should be fixed before this change lands.
  - P2: normal-priority defect.
  - P3: low-priority suggestion.
- Report only P0 and P1 findings. Do not post P2 or P3 findings, inline or
  in the review summary. Do not inflate priority to get a finding reported.
- A missing detail or an optional improvement is never P1. This applies to
  code and to documentation. A P1 is something wrong: a defect, or a false
  statement.
- On later reviews, verify that the earlier fixes work. Check their effects on
  callers and dependencies.
- When review history supports it, identify a finding as:
  - Regression: introduced since the previous reviewed head.
  - Unresolved: a previously reported defect remains.
  - Late discovery: present at a previously reviewed head but not reported.
    Only a P0 or P1 may be a late discovery.
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
- Decline a late discovery that is not P0 or P1. If the defect existed at the
  previous reviewed head and the new commits do not change its behavior,
  reply that the review rules exclude it, and do not fix it. A declined
  finding does not count as a fix round.
- Keep each fix to what its finding needs. Do not rewrite or extend nearby
  code or text. A change beyond the finding gives the next review new
  material to report, and the PR does not converge.
- Fix the underlying invariant across relevant call sites. Test the
  failure class rather than only the reported example.
- Preserve accepted fixes and regression tests when reviewing a rebased PR or
  when replacing or simplifying its implementation. Remove a test only when
  the behavior it protects is intentionally changed or removed, and explain
  that decision.
- Batch related fixes into one tested update before requesting re-review.
  Avoid duplicate review requests for the same head.
- An automated fixer pushes ordinary commits to the existing PR branch and
  runs every required gate. It does not bypass gates, merge, or change
  workflows, permissions, credentials, or branch protection. It force-pushes
  only after a rebase, as the next rule says. Report an environment failure
  as a blocker.
- When a PR branch conflicts with its base branch, the automated fixer
  rebases it onto the current base branch. The base branch is normally
  `main`. It resolves the conflicts, runs every required gate, and pushes
  with `git push --force-with-lease`. It does not merge the base branch into
  the PR branch. PR branches are rarely shared, so rewritten history is
  acceptable. Report a conflict as a blocker when both sides change the same
  logic and each resolution loses behavior. A rebase with no other change is
  not a fix round.
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

