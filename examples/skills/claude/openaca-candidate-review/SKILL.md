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
6. **Summarize.** Report:
   - Number of candidates reviewed / annotated / re-reviewed.
   - Any candidates left as-is (and why).
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
