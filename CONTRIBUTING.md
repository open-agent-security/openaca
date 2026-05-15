# Contributing to OpenACA

OpenACA is an open-source overlay corpus and reference scanner.
Contributions are welcome: new overlays, parser improvements, schema clarifications,
documentation, and bug fixes.

This guide covers the most common contribution flow: filing or
updating an overlay.

## Before you start

- Read [`docs/specs/openaca-thesis.md`](docs/specs/openaca-thesis.md)
  for what OpenACA is and the V0 → V1 roadmap, and
  [`docs/adrs/0009-overlay-only-v0.md`](docs/adrs/0009-overlay-only-v0.md)
  for the overlay-only V0 architecture.
- Read [`CLAUDE.md`](CLAUDE.md) for project-wide conventions, including
  the OSS-only scope rules.
- For security reports of active vulnerabilities, follow
  [`SECURITY.md`](SECURITY.md). Do not file overlays for undisclosed
  vulnerabilities as public PRs before coordinated disclosure has run.

## Project setup

The project uses [`uv`](https://docs.astral.sh/uv/) for environment
and dependency management. Install uv first if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or: brew install uv
```

Then:

```bash
git clone git@github.com:open-agent-security/openaca.git
cd openaca
uv sync
```

`uv sync` reads `.python-version` and `uv.lock`, creates a `.venv`,
and installs all runtime + dev dependencies.

You should now have these CLIs (invoked via `uv run`):

- `uv run openaca lint <path>` — validate overlays.
- `uv run openaca export` — build the static export under `dist/`.
- `uv run openaca scan repo --target REPO --sarif OUT` — run the
  reference scanner against a repository.
- `uv run openaca scan endpoint --project REPO` — run the reference
  scanner against a Claude Code endpoint context.

Run the test suite:

```bash
uv run pytest
```

## Filing an overlay

1. **Start from an upstream vulnerability ID.** V0 overlays use the
   upstream OSV/GHSA/CVE identifier as the file name:
   `overlays/GHSA-XXXX-YYYY-ZZZZ.yaml` or `overlays/CVE-YYYY-NNNN.yaml`.
   Do not mint an `OpenACA-YYYY-NNNN` ID in V0.

2. **Write only the OpenACA overlay block.** Vulnerability identity,
   affected ranges, severity, fixes, summary, and details come from the
   upstream OSV/GHSA/CVE record. OpenACA overlays add reviewed
   agent-context metadata:
   - `taxonomies`: standards-based taxonomy mappings such as
     `owasp_agentic_top10` (`asi01`–`asi10`) and `owasp_mcp_top10`
     (`mcp01:2025`–`mcp10:2025`).
   - `evidence_level`: `confirmed` | `likely` | `research` |
     `disputed` | `withdrawn`.
   - `threat_kind`: optional; V0 currently allows `malicious_package`.

   Minimal shape:
   ```yaml
   schema_version: "1.7.1"
   id: GHSA-XXXX-YYYY-ZZZZ
   aliases:
     - CVE-YYYY-NNNN
   modified: "2026-05-12T00:00:00Z"
   database_specific:
     openaca:
       taxonomies:
         owasp_agentic_top10:
           - asi02
       evidence_level: confirmed
   ```

3. **Optionally seed candidates from an OSV dump**:
   ```bash
   bash scripts/seed-osv-overlays.sh

   uv run openaca seed /path/to/osv/all.zip
   uv run openaca seed --modified-index /path/to/osv/modified_id.csv \
     --records-root /path/to/osv --state .openaca-seed-state.json
   uv run openaca seed /path/to/osv/all.zip \
     --llm-provider openai --llm-model "<model-name>"
   uv run openaca promote candidates/GHSA-XXXX-YYYY-ZZZZ.yaml
   ```
   The script downloads the npm and PyPI `modified_id.csv` + `all.zip`
   dumps into `${OPENACA_OSV_CACHE_DIR:-$TMPDIR/openaca-osv}` and advances the
   committed per-ecosystem seed cursors. Review and edit candidate YAML
   before promotion. `openaca promote` writes a minimal canonical overlay
   under `overlays/`.

   To use LLM-assisted annotation with the scripted workflow, set a
   supported provider, model, and API key:

   ```bash
   OPENACA_LLM_PROVIDER=openai \
   OPENACA_LLM_MODEL="<model-name>" \
   OPENACA_LLM_API_KEY="<api-key>" \
     bash scripts/seed-osv-overlays.sh
   ```

   `OPENACA_LLM_PROVIDER` accepts `openai` or `anthropic`. LLM mode
   receives the OSV record plus `docs/frameworks/*.md` as classification
   context. It still writes candidates only; every canonical overlay
   must be reviewed and promoted explicitly.

4. **Lint locally**:
   ```bash
   uv run openaca lint overlays/GHSA-XXXX-YYYY-ZZZZ.yaml
   ```
   Fix any failures before opening a PR.

5. **Open a PR** with the overlay file and a one-paragraph summary
   of what agent context the overlay adds.

### Bulk annotation via Claude Code

For human-in-the-loop triage of many candidates at once, without
burning API credits:

1. Run the deterministic seeder to populate `candidates/` (the wrapper
   downloads the npm and PyPI OSV dumps and advances the per-ecosystem
   cursors):
   ```
   bash scripts/seed-osv-overlays.sh
   ```
2. Copy the skill template into your Claude Code skills directory:
   ```
   cp -r examples/skills/claude/openaca-candidate-review ~/.claude/skills/
   ```
3. From a Claude Code session in this repo, invoke the skill:
   ```
   /openaca-candidate-review candidates/
   ```
   The agent reads `docs/seed-review-rules.md` and
   `docs/frameworks/*.md`, applies them to each candidate, and runs
   `openaca lint` on the result. See
   [`docs/seed-review-rules.md`](docs/seed-review-rules.md) for the
   exact editable surface (`taxonomies` + `evidence_level` only;
   everything else is filled by the seeder or comes from upstream).

API-mode annotation (`--llm-provider openai|anthropic`) remains
available for CI and batch runs.

## Linter discipline

The CI linter has two tiers:

**Hard fail** (your PR will not merge):

- Schema validation.
- File name matches `id`.
- OWASP ASI categories are valid (`asi01`–`asi10`).
- Internal cross-references resolve.

**Warning only** (separate scheduled job):

- Link liveness.
- OSV/GHSA enrichment via remote APIs.
- Remote alias resolution.

Don't try to "fix" warnings on every PR. They run nightly against the
full corpus, so transient remote-API failures don't block authors.

## Aliasing policy

- List equivalent upstream IDs in `aliases[]` so the scanner can merge
  overlays with OSV records by alias graph.
- V0 does not mint OpenACA vulnerability IDs. If a vulnerability has no
  upstream ID, use upstream disclosure channels first; an
  OpenACA-native record lane requires a later governance decision.

## Code contributions (parsers, linter, scanner)

- Follow TDD for non-trivial logic. Write the failing test first.
- Match existing code style; no unrelated refactors.
- Default to writing no comments. Add one only when *why* is
  non-obvious.
- One logical change per commit. Commit messages focus on *why*, not
  *what*.
- Run `uv run pytest` and `uv run ruff check tools/ tests/` before
  opening a PR.

### Adding a posture rule

Posture rules are scanner-emitted hygiene checks ([`docs/posture/`](docs/posture/README.md)).
A new rule needs:

1. **Rule module:** `tools/posture/rules/<short_name>.py` exporting a
   single `check_<short_name>(...)` function that returns
   `list[PostureFinding]`. Module-level constants: `RULE_ID`, `TITLE`,
   `SEVERITY` (`"low"` | `"medium"` | `"high"`), `CONFIDENCE`,
   `REMEDIATION`, plus a `_STANDARDS = Standards(...)` block populated
   with whatever taxonomy families apply.
2. **Rule ID:** prefix with `openaca-posture-` and use kebab-case
   (e.g., `openaca-posture-mutable-install-reference`).
3. **Standards mapping:** carry every family that legitimately applies.
   Don't force a CWE if the fit is poor — leave it empty. Agentic and
   MCP-specific codes (`asiNN`, `mcpNN:2025`) are the agent-context
   layer on top of the primary CWE/Scorecard/SLSA mapping.
4. **Registration:** import the rule module from
   `tools/posture/__init__.py` and call its check function from
   `run_posture_rules(...)`.
5. **Tests:** `tests/test_posture_<short_name>.py` covers (a) the
   trigger case, (b) the obvious non-trigger case, (c) the
   false-positive case that should NOT be flagged, and (d) the
   standards block. Reuse existing fixtures where possible.
6. **Docs:** `docs/posture/<rule_id>.md` follows the existing template
   — what triggers it, why it matters (with the standards table), how
   to fix (with concrete examples), when to suppress.

The rule is disabled until a user passes `--include-posture`. Default
to conservative severity for new rules; tune up only after the
false-positive shape is well understood in real-world dogfooding.

## What does not belong in this repo

Per [`CLAUDE.md`](CLAUDE.md), all artifacts here are OSS-focused. Do
not include in PRs:

- Commercial product plans or monetization framings.
- Comparisons against vendor products positioning OpenACA as a
  competitor.
- Market analysis, sales narratives, go-to-market content.
- Vendor names framed as competitors. (Naming a tool we *use* with
  attribution is fine; naming a product as a competitor is not.)

If a draft contains content in those categories, rewrite to remove.
When unsure, ask in the PR.

## Code of conduct

Treat reporters, maintainers, and affected projects with respect.
Disagree on technical merits, not on people. Coordinated disclosure
is collaboration with affected maintainers — approach it as such.
