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
   `.claude/settings.json`. Cursor/Windsurf manifests are V1.
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
before pushing.

When you encounter an obstacle, don't use destructive actions as a
shortcut to make it go away. Identify the root cause; fix the
underlying issue rather than bypassing safety checks (e.g.,
`--no-verify`).

If you discover unexpected state — unfamiliar files, branches,
configuration — investigate before deleting or overwriting. It may
represent the user's in-progress work.
