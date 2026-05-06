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

### Working in worktrees (default)

Feature branches live in `.worktrees/<branch-name>/`, not as branches checked
out in the main repo. Exceptions: tiny one-shot edits to default-branch-only
files (`CLAUDE.md`, `README.md` typos, ADR `INDEX.md` one-liners).

### Commits

- Frequent commits, one logical change per commit.
- Commit messages: focus on *why*, not *what*.
- Only create commits when the user requests one. If unclear, ask.

### TDD for non-trivial logic

Write the failing test first for parsers, linter rules, and the importer.
Skip TDD for throwaway scripts, config (`Makefile`, CI YAML), and
trivial fixes.

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

