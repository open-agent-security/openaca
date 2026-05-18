---
name: openaca-candidate-review
description: Annotate or re-review OpenACA seed candidates in `candidates/` according to the canonical rules. Use when the user asks to annotate, review, fix, or re-classify candidate YAML files produced by the deterministic seeder.
---

# OpenACA candidate review

Annotate or re-review reviewable seed candidates produced by
`openaca seed`. Uses your Claude Code session's auth, not the API
LLM provider path.

## Invocation modes

- `/openaca-candidate-review candidates/GHSA-mq53-pc65-wjc4.yaml` — single file
- `/openaca-candidate-review candidates/` — every candidate whose
  `_candidate.review_status` is `needs_review`

For directory mode, if there are more than 20 candidates to review,
stop and ask the user to split into smaller batches; quality
degrades past ~20 records per session due to context budget.

## What to do

1. **Locate the OpenACA repo.** It is the working directory if the
   user invoked from inside it; otherwise ask once.
2. **Read the rules.** Read `docs/seed-review-rules.md` in full.
   This is the canonical contract. If the file is missing, stop and
   report — the repo is not in a state to be reviewed.
3. **Read the framework references.** Read all of
   `docs/frameworks/*.md` (typically OWASP Agentic Top 10, OWASP LLM
   Top 10, OWASP MCP Top 10, MITRE ATLAS, OWASP Agentic Skills Top
   10). Hold them as context for taxonomy mapping decisions.
4. **For each candidate file:**
   - Read the file.
   - Determine review mode: `annotate` (no
     `database_specific.openaca`) or `re-review` (block exists).
   - Read the OSV record fields in the file (`summary`, `details`,
     `affected`, `references`).
   - Apply the rules from `docs/seed-review-rules.md`:
     - Edit ONLY `database_specific.openaca.taxonomies.*` and
       `database_specific.openaca.evidence_level`.
     - NEVER touch `threat_kind`, `_candidate`, `_evidence`,
       `summary`, `details`, `affected`, `severity`, `references`,
       `aliases`, `id`, `modified`.
     - Omit taxonomy keys that do not apply. Do not emit `[]` or
       `{}`.
     - Do not classify ordinary bugs in agent-stack packages as
       supply-chain (`asi04`, `llm03:2025`, ATLAS supply-chain
       techniques) unless the OSV record describes malicious,
       compromised, typosquat, or dependency-confusion behavior.
     - For each taxonomy assertion, hold a defensible quote from
       the OSV `summary`/`details` in mind. Do not assert mappings
       you cannot defend.
   - Write the edited YAML back to the same path using the Edit
     tool.
5. **Validate.** After all edits, run:
   ```
   uv run openaca lint candidates/
   ```
   If validation fails, read the error message, correct the
   relevant candidate, and re-run validation. Do not move on with
   unresolved validation errors.
6. **Internal audit pass (before user review).** Dispatch three
   parallel subagents (single tool-call block, type
   `general-purpose`, read-only) that each audit the batch for one
   recurring systematic error. These three error classes were
   caught repeatedly by external review across early batches; the
   audit step is the cheaper, in-band catch.

   - **Audit 1 — ATLAS `T0010.005` overreach.** For every batch
     record whose taxonomies include `AML.T0010.005` (AI Agent
     Tool), check whether the OSV evidence describes the package
     as an MCP server, tool plugin, or something an agent
     installs or connects to (`docs/frameworks/mitre-atlas.md`
     supply-chain rules). `T0010.005` is the correct default
     for malicious MCP servers and malicious tool plugins — do
     not flag these for downgrade. Flag for downgrade to
     `AML.T0010.001` (AI Software, general) and removal of
     `AML.T0104` (Publish Poisoned AI Agent Tool) only when the
     OSV record describes a general AI library, LLM framework,
     or developer tooling (CLI / inspector / SDK / lib /
     scaffolder / `*-dev-*`) with no server or agent-tool
     evidence.
   - **Audit 2 — `AML.T0074` (Masquerading) missing/over-applied.**
     For every batch record, check the package name for typosquat
     fingerprints: exact upstream leaf under a non-canonical scope
     (e.g., `@x/claude-code` typosquats `@anthropic-ai/claude-code`,
     `@upstashed/context7-mcp` typosquats `@upstash/context7-mcp`),
     doubled tokens, misspelled brand prefixes, known brand names
     under unknown scopes. Cross-reference OSV `_evidence` and
     source attestations for explicit "typosquat" / "impersonation"
     / "namesquat" / "brand" callouts. Flag missing-T0074 adds and
     evidence-thin-T0074 removes.
   - **Audit 3 — `mcp03:2025` / `mcp10:2025` payload location.**
     For every batch record whose taxonomies include `mcp03:2025`
     (Tool Poisoning) or `mcp10:2025` (Context Injection and
     Over-Sharing), read the OSV `summary` and `details`. `mcp03:2025`
     requires explicit mention of payload in tool descriptions /
     schemas / manifests / metadata. `mcp10:2025` requires explicit
     mention of payload in tool output / retrieved context /
     over-shared resources. If neither location is named, flag the
     code for removal.

   Apply the audit findings as another bulk edit pass, then re-run
   `uv run openaca lint candidates/` to confirm structural validity
   after revisions.
7. **Move approved candidates to `candidates/ready_for_review/`.**
   After annotation + lint + audit are clean, `mv` each candidate
   from `candidates/<id>.yaml` to `candidates/ready_for_review/<id>.yaml`.
   This is the exit gate of the skill's internal pipeline: a
   candidate that reaches `ready_for_review/` has passed all
   in-skill checks and is staged for external (user/Codex) review.

   Use `mv`, not `cp` — a candidate exists in exactly one place at
   any time. `candidates/` root is volatile (the seeder writes there
   unconditionally and may overwrite on reseed); `ready_for_review/`
   is the tracked, stable review queue. Two copies would risk
   divergence on the next reseed.

   Operationally:
   ```bash
   mkdir -p candidates/ready_for_review
   for f in <list of approved candidate filenames>; do
     mv "candidates/${f}" "candidates/ready_for_review/${f}"
   done
   ```

   If a candidate failed lint or audit and was reverted/rejected, it
   stays in `candidates/` root and the user gets a note in the
   summary.
8. **Summarize.** Report:
   - Number of candidates reviewed / annotated / re-reviewed.
   - Pattern groupings applied (e.g., "8 records mapped as MCP
     server / supply-chain shape").
   - Audit findings and revisions applied.
   - Number of candidates moved to `ready_for_review/`.
   - Any candidates left in `candidates/` root (and why — usually
     unresolved validation/audit issues or out-of-scope routing).
   - Any validation failures (and the corrective edit applied).

## Out of scope

- Do not edit overlays under `overlays/` from this skill. Use
  `openaca promote` for promotion after candidate review is complete.
- Do not change OSV-owned fields. If the OSV record itself looks
  wrong, surface it to the user rather than editing the candidate.
- Do not invent taxonomies that are not in
  `docs/frameworks/*.md`.

## When the rules and your judgment disagree

The rules win. If a rule prohibits something you believe should be
allowed, finish the current review with the existing rules and
then surface the disagreement to the user in your summary message.
Do not silently apply your own judgment over the documented rule.
