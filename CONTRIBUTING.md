# Contributing to ASVE

ASVE is an open-source advisory database. Contributions are welcome:
new advisories, parser improvements, schema clarifications,
documentation, and bug fixes.

This guide covers the most common contribution flow: filing or
updating an advisory.

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

- `uv run asve-lint <path>` — validate advisories.
- `uv run asve-reserve-id <advisories-dir> --year YYYY` — print the
  next free ID.
- `uv run asve-import-osv --osv-file FILE --asve-id ID --out PATH` —
  generate an advisory skeleton from an OSV record.
- `uv run asve-export` — build the static export under `dist/`.
- `uv run asve-scan --target REPO --advisories DIR --sarif OUT` — run
  the reference scanner.

Run the test suite:

```bash
uv run pytest
```

## Filing an advisory

1. **Reserve an ID** for the current year:
   ```bash
   uv run asve-reserve-id advisories/ --year 2026
   # ASVE-2026-NNNN
   ```

2. **Generate a skeleton** if the vulnerability already has an OSV/GHSA
   record:
   ```bash
   uv run asve-import-osv --osv-id GHSA-XXXX-YYYY-ZZZZ \
                          --asve-id ASVE-2026-NNNN \
                          --out advisories/2026/ASVE-2026-NNNN.yaml
   ```
   Or hand-write the YAML using
   [`tests/fixtures/valid/asve-2026-0001.yaml`](tests/fixtures/valid/asve-2026-0001.yaml)
   as a model.

3. **Fill in the `database_specific.asve` block** — this is what
   distinguishes an ASVE record from a passthrough alias:
   - `component_type` (e.g., `mcp_server`, `claude_plugin`,
     `model_proxy`, `agent_framework`, `skill_bundle`).
   - `surfaces`: which agent surfaces the component touches.
   - `agent_impact`: boolean table of attainable impacts.
   - `owasp_agentic_top10`: array of `asi01`–`asi10` categories.
   - `evidence_level`: `confirmed` | `likely` | `research` |
     `disputed` | `withdrawn`.

   **Recognized `affected[*].package.ecosystem` values** the matcher
   currently understands:
   - `npm`, `PyPI`, `GitHub`, `Docker` — standard PURL ecosystems.
   - `claude-plugin` — Claude Code plugins identified by `name` from
     the plugin's `.claude-plugin/plugin.json` (per ADR-0006). Plugin
     advisories use this ecosystem; `_match_versioned` handles the
     range matching identically to npm/PyPI.
   - `claude-skill` — Agent skills identified by `name` from
     `SKILL.md` frontmatter, with optional version via
     `metadata.version` (per ADR-0007). Range matching same as
     `claude-plugin`. Use this for SKILL.md-shaped surfaces.
   - `claude-hook` — Hook entries identified by JSON-path slot:
     `claude-hook/<plugin>/<event>/<index>` for plugin-bundled or
     `claude-hook/settings/<scope>/<event>/<index>` for
     settings-scoped (scope ∈ user|project|local). V0 is
     identity-only matching against
     `database_specific.asve.component_identity` — no range algebra,
     since hooks don't have a versioning convention.
   - `claude-command`, `claude-agent` — Slash commands and subagents
     identified by `<eco>/<owner>/<name>`, where `<owner>` is the
     plugin name (bundled) or the literal `repo` (declared in
     `.claude/commands/` or `.claude/agents/`). V0 is identity-only
     matching.

4. **Add a CVSS v4 vector** under `severity[]` if known:
   ```yaml
   severity:
     - type: CVSS_V4
       score: "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N"
   ```

5. **Lint locally**:
   ```bash
   uv run asve-lint advisories/2026/ASVE-2026-NNNN.yaml
   ```
   Fix any failures before opening a PR.

6. **Open a PR** with the advisory file and a one-paragraph summary
   of what the advisory covers.

## V0 record-type policy

V0 accepts only `type: vulnerability` records. PRs proposing
`type: exposure` or `type: config` records are closed with a pointer
to the V1 methodology track. The schema reserves these values;
runtime acceptance is gated by an explicit V1 process.

## Linter discipline

The CI linter has two tiers:

**Hard fail** (your PR will not merge):

- Schema validation.
- ID format and uniqueness within `ASVE-YYYY-NNNN`.
- Required fields per `type`.
- CVSS v4 vector parses.
- OWASP ASI categories are valid (`asi01`–`asi10`).
- File path matches ID year and number.
- Internal cross-references resolve.

**Warning only** (separate scheduled job):

- Link liveness.
- OSV/GHSA enrichment via remote APIs.
- Remote alias resolution.

Don't try to "fix" warnings on every PR. They run nightly against the
full corpus, so transient remote-API failures don't block authors.

## Aliasing policy

- If your advisory aliases an existing CVE/GHSA/OSV, list the upstream
  IDs in `aliases[]`. ASVE creates the alias and overlays
  agent-context metadata. **No new upstream filing required.**
- If your advisory is ASVE-original, attempt upstream disclosure to
  CVE/GHSA where the affected ecosystem is accepted upstream. ASVE
  will carry the authoritative record only when upstream pipelines
  don't fit.

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
