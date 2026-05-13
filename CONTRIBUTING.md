# Contributing to ASVE

ASVE is an open-source overlay corpus and reference scanner.
Contributions are welcome: new overlays, parser improvements, schema clarifications,
documentation, and bug fixes.

This guide covers the most common contribution flow: filing or
updating an overlay.

## Before you start

- Read [`docs/specs/asve-v0-design.md`](docs/specs/asve-v0-design.md)
  for the V0 scope and architecture.
- Read [`CLAUDE.md`](CLAUDE.md) for project-wide conventions, including
  the OSS-only scope rules.
- For security reports of active vulnerabilities, follow
  [`SECURITY.md`](SECURITY.md). Do not file new advisories as public
  PRs before coordinated disclosure has run.

## Project setup

The project uses [`uv`](https://docs.astral.sh/uv/) for environment
and dependency management. Install uv first if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # or: brew install uv
```

Then:

```bash
git clone git@github.com:open-agent-security/asve.git
cd asve
uv sync
```

`uv sync` reads `.python-version` and `uv.lock`, creates a `.venv`,
and installs all runtime + dev dependencies.

You should now have these CLIs (invoked via `uv run`):

- `uv run asve-lint <path>` — validate overlays.
- `uv run asve-export` — build the static export under `dist/`.
- `uv run asve-scan repo --target REPO --sarif OUT` — run the
  reference scanner against a repository.
- `uv run asve-scan endpoint --project REPO` — run the reference
  scanner against a Claude Code endpoint context.

Run the test suite:

```bash
uv run pytest
```

## Filing an overlay

1. **Start from an upstream vulnerability ID.** V0 overlays use the
   upstream OSV/GHSA/CVE identifier as the file name:
   `overlays/GHSA-XXXX-YYYY-ZZZZ.yaml` or `overlays/CVE-YYYY-NNNN.yaml`.
   Do not mint an `ASVE-YYYY-NNNN` ID in V0.

2. **Write only the ASVE agent-context block.** OSV/GHSA/CVE owns
   vulnerability identity, affected ranges, severity, fixes, summary,
   and details. ASVE overlays add agent-context metadata:
   - `component_type` (e.g., `mcp_server`, `claude_plugin`,
     `model_proxy`, `agent_framework`, `skill_bundle`).
   - `surfaces`: which agent surfaces the component touches.
   - `agent_impact`: boolean table of attainable impacts.
   - `taxonomies`: agent-context taxonomy mappings such as
     `owasp_agentic_top10` (`asi01`–`asi10`) and `owasp_mcp_top10`
     (`mcp01:2025`–`mcp10:2025`).
   - `evidence_level`: `confirmed` | `likely` | `research` |
     `disputed` | `withdrawn`.

   Minimal shape:
   ```yaml
   schema_version: "1.7.1"
   id: GHSA-XXXX-YYYY-ZZZZ
   aliases:
     - CVE-YYYY-NNNN
   modified: "2026-05-12T00:00:00Z"
   database_specific:
     asve:
       component_type: mcp_server
       surfaces:
         - tool_invocation
       agent_impact:
         credential_exfiltration: false
         repo_write: false
         command_execution: true
       taxonomies:
         owasp_agentic_top10:
           - asi02
       evidence_level: confirmed
   ```

3. **Optionally seed candidates from an OSV dump**:
   ```bash
   bash scripts/seed-osv-overlays.sh

   uv run asve-seed /path/to/osv/all.zip
   uv run asve-seed --modified-index /path/to/osv/modified_id.csv \
     --records-root /path/to/osv --state .asve-seed-state.json
   uv run asve-seed /path/to/osv/all.zip \
     --llm-provider openai --llm-model "<model-name>"
   uv run asve-promote candidates/GHSA-XXXX-YYYY-ZZZZ.yaml
   ```
   The script downloads the npm and PyPI `modified_id.csv` + `all.zip`
   dumps into `${ASVE_OSV_CACHE_DIR:-$TMPDIR/asve-osv}` and advances the
   committed per-ecosystem seed cursors. Review and edit candidate YAML
   before promotion. `asve-promote` writes a minimal canonical overlay
   under `overlays/`.

   To use LLM-assisted annotation with the scripted workflow, set a
   supported provider, model, and API key:

   ```bash
   ASVE_LLM_PROVIDER=openai \
   ASVE_LLM_MODEL="<model-name>" \
   ASVE_LLM_API_KEY="<api-key>" \
     bash scripts/seed-osv-overlays.sh
   ```

   `ASVE_LLM_PROVIDER` accepts `openai` or `anthropic`. LLM mode
   receives the OSV record plus `docs/frameworks/*.md` as classification
   context. It still writes candidates only; every canonical overlay
   must be reviewed and promoted explicitly.

4. **Lint locally**:
   ```bash
   uv run asve-lint overlays/GHSA-XXXX-YYYY-ZZZZ.yaml
   ```
   Fix any failures before opening a PR.

5. **Open a PR** with the overlay file and a one-paragraph summary
   of what agent context the overlay adds.

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
- V0 does not carry ASVE-original vulnerability records. If a
  vulnerability has no upstream ID, use upstream disclosure channels
  first; an ASVE-native advisory lane requires a later governance
  decision.

## Code contributions (parsers, linter, scanner)

- Follow TDD for non-trivial logic. Write the failing test first.
- Match existing code style; no unrelated refactors.
- Default to writing no comments. Add one only when *why* is
  non-obvious.
- One logical change per commit. Commit messages focus on *why*, not
  *what*.
- Run `uv run pytest` and `uv run ruff check tools/ tests/` before
  opening a PR.

## What does not belong in this repo

Per [`CLAUDE.md`](CLAUDE.md), all artifacts here are OSS-focused. Do
not include in PRs:

- Commercial product plans or monetization framings.
- Comparisons against vendor products positioning ASVE as a
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
