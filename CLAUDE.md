# ASVE — Project Conventions for Claude

ASVE (Agent Stack Vulnerabilities and Exposures) is an open-source, OSV-compatible
advisory database for AI agent infrastructure: plugins, MCP servers, skills, agent
frameworks, model proxies, and runtime components.

## Project scope: OSS only

This repository contains the open-source advisory database, schema, parsers, and
reference scanner. **All project artifacts in this repo are OSS-focused.**

When writing or editing any file in this repo (specs, plans, READMEs, ADRs,
advisories, code, comments, commit messages, PR descriptions), do **not** include:

- Commercial product plans, monetization strategies, pricing, paid-tier features.
- Vendor comparisons or competitive positioning (e.g., "better than X," "unlike Y").
- Market analysis, go-to-market narratives, sales framing.
- Vendor names framed as *competitors*. Naming a tool we *use* is fine; naming a
  product as a competitor is not.

If a draft contains content in these categories, rewrite to remove. When uncertain
whether something falls in scope, prefer to omit and flag for human review.

ASVE's authority depends on positioning as a neutral, vendor-agnostic public
advisory layer. Commercial or competitive framing in OSS artifacts erodes that.

### What is in scope

- Operational decisions that reference external tools by name where attribution
  is required (e.g., "advisory detected during ASVE triage using
  `cisco-ai-defense/mcp-scanner` v0.X").
- Technical interoperability (aliasing CVE/GHSA records, adopting OWASP Agentic
  Top 10 categories, using the OSV schema).
- Cross-project collaboration notes (engaging OSV.dev, MCP TSC, OpenSSF, etc.).

The test: is this content describing *what ASVE does or how it interoperates*, or
is it positioning ASVE *against* something? The first is fine; the second is out.

## Repository layout

- `docs/specs/asve-v0-design.md` — canonical V0 design.
- `docs/plans/NNN-<topic>.md` — one implementation plan per V0 deliverable.
- `docs/adrs/NNNN-<topic>.md` — durable architecture decisions.
- `schema/asve.schema.json` — canonical advisory schema.
- `advisories/YYYY/ASVE-YYYY-NNNN.yaml` — the advisory corpus.
- `tools/` — linter, ID reservation, OSV importer, static export.
- `action.yml` — reference GitHub Action at repo root.
- `CONTRIBUTING.md` — contributor flow, advisory authoring guide.

## Conventions

### Authoring

- Default to writing no comments. Add one only when the *why* is non-obvious.
- Match existing file style; surgical edits only. Don't refactor adjacent code
  unless the task requires it.
- Every advisory ships with reproducible evidence where possible (vulnerable
  config snippet, malicious tool description, affected command). Treat fixtures
  as advisory metadata, not a separate corpus.

### Schema and IDs

- Single namespace: `ASVE-YYYY-NNNN`.
- Type-tagged records: `type: vulnerability` (V0); `type: exposure` and
  `type: config` are reserved in schema but **rejected in V0 PRs** pending
  methodology docs.
- Severity: CVSS v4 base+environmental.
- Category: `owasp_agentic_top10[]` array referencing ASI01–ASI10.
- Agent context: `database_specific.asve.{component_type, surfaces[], agent_impact{}}`.
- No custom severity enum (no `agent_blast_radius` or similar parallel taxonomy).

### Linter discipline (CI)

- **Hard fail**: schema validation, ID format/uniqueness, required fields,
  CVSS parses, ASI category validity, internal cross-references resolve.
- **Warning / scheduled job (don't block PRs)**: link liveness, OSV/GHSA
  enrichment, remote alias resolution. External APIs are flaky; PRs shouldn't
  fail because of them.

### Aliasing policy

- Records aliasing existing CVE/GHSA/OSV: ASVE creates the alias and overlays
  agent-context metadata. No upstream filing required.
- ASVE-original component vulnerabilities: attempt upstream disclosure to
  CVE/GHSA where the affected ecosystem accepts it. Where upstream pipelines
  don't accept the ecosystem cleanly, ASVE carries the authoritative record.

## V0 scope (read `docs/specs/asve-v0-design.md` for detail)

V0 ships:

1. Schema with `type` field branching per-type required fields.
2. Manifest parsers for `package.json`, `mcp.json`, `.claude-plugin/plugin.json`,
   `.claude/settings.json`. Cursor/Windsurf manifests are V1.
3. 3-5 hand-curated `type: vulnerability` advisories (mostly CVE/GHSA aliases,
   ≥1 enriched with manifest detection that catches what lockfile-only SCA
   misses).
4. Linter + CI per discipline above.
5. Static export pipeline: `advisories/*.yaml → JSON → all.zip → modified_id.csv`.
6. Reference GitHub Action: `open-agent-security/asve@v1` with `action.yml` at
   repo root.
7. Disclosure policy doc (OpenSSF baseline + ASVE-specific defaults).
   **V0 documents the policy; does not operate it at scale.**

V0 does **not** ship: HTTP API, benchmark harness, public detection-rule format,
custom CLI binary, active disclosure pipeline, `type: exposure` or `type: config`
records.


---

## Behavioral guidelines

**Tradeoff:** these bias toward caution over speed. For trivial tasks,
use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Test: would a senior engineer say this is overcomplicated? If yes,
simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's
request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

### 5. Verify Before Claiming Done

**Evidence before assertions, always.**

- Run the tests; don't say "should pass" — say "passed" only after the
  command exited green.
- If you can't run the verification (no UI access, no test infra), say
  so explicitly. Don't claim success based on type-checks alone.
- Adversarial review before declaring done: re-read the diff and ask
  "what did I miss? what did I assume? does this test the problem
  space or just my implementation?"

## Working in worktrees (default for non-trivial branches)

Feature/bug/refactor branches live in `.worktrees/<branch-name>/`,
not as a branch checked out in the main repo.

**Why:** the main repo's working tree stays on the default branch so
long-running local processes (dev servers, demo loops, services-up
scripts) keep running while feature work happens elsewhere on the
same machine. Also lets `cd` into another worktree to inspect a
different branch without disrupting the current workspace.

**When to apply:**
- New branch → `git worktree add .worktrees/<branch> -b <branch>`
- Resuming an existing local branch → if `git worktree list` shows it,
  `cd` there; else `git worktree add .worktrees/<branch> <branch>`
- Tracking a remote branch to inspect locally →
  `git worktree add .worktrees/<branch> origin/<branch>`

**Exceptions (commit on the main worktree directly):**
- Tiny one-shot edits to default-branch-only files: `.gitignore`,
  `CLAUDE.md`, ADR INDEX one-liners, README typos.
- Work the user explicitly asks for "on the current branch" inside an
  existing worktree.

**Cleanup:** after a branch lands, `git worktree remove
.worktrees/<branch>`. Branch ref deletes separately with `git branch
-d <branch>` if no longer useful.

Ensure `.worktrees/` is in `.gitignore`.

## Architecture Decision Records (ADRs)

Durable design notes belong in `docs/adrs/`.

**When to write a new ADR:** when you make a decision where (a) the
rejected alternative is *plausible*, (b) it's *likely to be
re-suggested* (by a future you, a teammate, or another agent), and
(c) the reason isn't obvious from the code alone.

The bar matters. Most decisions don't clear it. The test: would a
future reviewer or agent, looking only at the code, plausibly suggest
the alternative we rejected? If yes, write the ADR.

**Read.** Before changing logic in an area an ADR covers, read the
full ADR. The cost of one Read is much smaller than the cost of
re-deriving or re-litigating a decision.

**Supersede, never edit.** Accepted ADRs are immutable. If a later
decision contradicts an existing ADR, write a NEW ADR with
`supersedes: NNNN` in its frontmatter, and update the old one's
frontmatter to `status: superseded` + `superseded-by: NNNN`. Old PRs
need to remain readable against the rules in effect at the time —
silently editing an accepted ADR breaks that contract.

## Plans for multi-step work

Long-running implementation work belongs in a plan file
(`docs/plans/NNN-<topic>.md` or similar) with `- [ ]` / `- [x]`
checkboxes. Source of truth for progress = the checkboxes + git
history. Don't track progress in CLAUDE.md or memory files; they go
stale.

For multi-plan projects, an index (`docs/plans/README.md`) with one
🟡 Active row at a time keeps "where am I" obvious across session
breaks.

Don't create a plan file for trivial work. The threshold is roughly
"more than one work session" — anything that survives a session break
needs durable state.

## Commits and PRs

- Frequent commits, one logical change per commit.
- Commit messages: focus on WHY (the motivation, the constraint), not
  WHAT (the diff already shows that).
- Push to remote at logical points; don't hoard local commits.
- Only create commits when the user requests one. If unclear, ask.
- Only push to a remote when the user requests it.
- **Finishing a feature branch: default to push + open a PR.** PR
  review is the merge path even on solo repos. Exceptions only when
  the user explicitly says "merge locally," "keep as branch," or
  "discard."

## TDD for business logic

For non-trivial business logic, write the failing test first, then
make it pass, then refactor. The bite-sized red/green/refactor/commit
cadence keeps the loop tight and the diff reviewable.

Skip TDD discipline for: throwaway scripts, exploratory spikes,
obvious one-line fixes, infrastructure config (Dockerfiles, CI YAML,
shell scripts where tests cost more than the change is worth).

## End-to-end tests live in `tests/test_e2e.py`

Unit tests live next to the code under test. **Cross-layer tests
that exercise multiple modules together against the real corpus
(`advisories/` + `schema/` + the parser/exporter modules) belong in
`tests/test_e2e.py`.**

Why a single growing file: cross-layer tests are about the *product
promise* (does ASVE actually detect a vulnerable agent component?),
not about any one module. A single file makes the suite trivial to
read, hard to lose, and naturally evolves as plans land.

When a plan adds a feature that crosses module boundaries, ask:
*what's the one-screen test that demonstrates this layer wiring up
correctly with what's already there?* Add it to `test_e2e.py`. Examples:

- Plan 005 (reference action) → action-invocation test: invoke the
  Action's CLI surface against a fixture repo, verify it finds the
  same advisory match the parser-only test 110 finds.
- Plan 006 (disclosure policy) → doc-only, no addition.
- A future "advisory diff" feature → diff-output test exercising
  the linter + the diff renderer + the corpus together.

Don't move existing unit tests here. Don't put e2e tests in module
test files. The boundary is: *does this test fail if any one of
several modules regresses?*

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

When you encounter an obstacle, don't use destructive actions as a
shortcut to make it go away. Identify the root cause; fix the
underlying issue rather than bypassing safety checks (e.g.,
`--no-verify`).

If you discover unexpected state — unfamiliar files, branches,
configuration — investigate before deleting or overwriting. It may
represent the user's in-progress work.
