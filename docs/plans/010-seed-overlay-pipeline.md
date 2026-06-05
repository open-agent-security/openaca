# Plan 010 — Seed Overlay Pipeline

**Goal:** Add a V0-safe workflow for seeding reviewed OpenACA overlays from OSV bulk dumps and OSV `modified_id.csv` indexes.

**Architecture:** Canonical `overlays/` stays scanner-visible and minimal. The seeder writes reviewable candidates to `candidates/`, outside the scanner/export path. Discovery remains deterministic; annotation can be deterministic or opt-in LLM-assisted with `docs/frameworks/*.md` as context. A human reviews and edits each candidate, then `openaca promote` projects it into the canonical overlay shape and validates it before writing `overlays/<id>.yaml`.

**Tech Stack:** Python 3.11, Click CLIs, PyYAML, JSON Schema, pytest, existing OpenACA schema/linter/export tooling.

---

### Task 1: ADR And Overlay Taxonomy Schema

**Files:**
- Create: `docs/adrs/0010-overlay-taxonomies-and-seeding.md`
- Modify: `docs/adrs/INDEX.md`
- Modify: `schema/openaca.schema.json`
- Modify: `overlays/*.yaml`
- Modify: `tools/templates/advisory.html.j2`
- Test: `tests/test_schema.py`

- [x] Write tests that require `database_specific.openaca.taxonomies` and allow `threat_kind`.
- [x] Run focused schema tests and confirm they fail before schema changes.
- [x] Update schema to add `taxonomies` and `threat_kind`.
- [x] Migrate existing overlays from flat `owasp_agentic_top10` to `taxonomies.owasp_agentic_top10`.
- [x] Update export HTML template to render taxonomy groups.
- [x] Add ADR-0010 documenting taxonomy shape, candidate-vs-overlay boundary, no LLM in V0, and no CWE duplication by default.
- [x] Run `uv run openaca lint overlays/` and focused schema/export tests.

### Task 2: Promotion Boundary

**Files:**
- Create: `tools/promote.py`
- Modify: `pyproject.toml`
- Test: `tests/test_promote.py`

- [x] Write tests showing `openaca promote candidates/GHSA-x.yaml` strips `_candidate`, evidence, upstream summaries/details, and other candidate-only fields.
- [x] Run focused promotion tests and confirm they fail.
- [x] Implement `project_candidate_to_overlay()` and Click CLI.
- [x] Validate promoted overlays with the canonical JSON schema.
- [x] Register `openaca promote` in `pyproject.toml`.
- [x] Run focused promotion tests.

### Task 3: Candidate Validator

**Files:**
- Create: `tools/seed/__init__.py`
- Create: `tools/seed/validator.py`
- Test: `tests/test_seed_validator.py`

- [x] Write validator tests for valid candidates, invalid taxonomy IDs, invalid impact shapes, missing candidate review metadata, and upstream-owned canonical fields that promotion must drop.
- [x] Run focused validator tests and confirm they fail.
- [x] Implement deterministic candidate validation helpers that reuse the canonical schema on the projected overlay.
- [x] Run focused validator tests.

### Task 4: Deterministic Seeder CLI

**Files:**
- Create: `tools/seed/__main__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_seed_cli.py`

- [x] Write CLI tests for deterministic discovery, candidate output, curated overlay dedup, MAL record handling, `modified_id.csv` incremental seeding, and dry-run output.
- [x] Run focused seeder tests and confirm they fail.
- [x] Implement OSV dump iteration, `modified_id.csv` incremental iteration, MCP/agent discovery heuristics, rule-based draft annotations, candidate validation, and `candidates/` output.
- [x] Register `openaca seed` in `pyproject.toml`.
- [x] Run focused seeder tests.

### Task 5: Full Verification And PR

**Files:**
- Modify as needed based on test fallout only.

- [x] Run `uv run pytest -q`.
- [x] Run `uv run ruff check .`.
- [x] Run `uv run ruff format --check .`.
- [x] Run `uv run pyright`.
- [x] Run `uv run openaca lint overlays/`.
- [x] Run `git diff --check`.
- [x] Review diff for accidental main-worktree spike carryover or canonical overlay noise.
- [x] Commit, push, and open a ready PR.

### Task 6: npm/PyPI Incremental Seed Workflow

**Files:**
- Create: `scripts/seed-osv-overlays.sh`
- Create: `.openaca seed-state-npm.json`
- Create: `.openaca seed-state-pypi.json`
- Modify: `tools/seed/__main__.py`
- Modify: `CONTRIBUTING.md`
- Test: `tests/test_seed_cli.py`
- Test: `tests/test_seed_workflow_script.py`

- [x] Write tests for `modified_id.csv` rows resolving from ecosystem `all.zip` files.
- [x] Write a script smoke test with fake `gcloud` and `uv`.
- [x] Teach incremental seeding to resolve records from extracted JSON, ecosystem `all.zip`, and root `all.zip`.
- [x] Add a repeatable script that downloads npm and PyPI OSV dumps and runs `openaca seed` with committed cursor files.
- [x] Document the scripted workflow.
- [x] Run focused seeder and script tests.
- [x] Run full verification before PR.

### Task 7: Deterministic Agent-Stack Discovery Expansion

**Files:**
- Modify: `tools/seed/__main__.py`
- Modify: `tests/test_seed_cli.py`

- [x] Write failing tests for known agent-stack packages without an `mcp` token.
- [x] Write failing tests for AI-feature topics in generic package names.
- [x] Add conservative package-name discovery patterns for agent frameworks, agent builders, coding agents, LLM gateways, SDKs, and vector/RAG components.
- [x] Add topic discovery for prompt injection, AI assistants, agent tools, memory tools, RAG poisoning, and related AI-feature wording.
- [x] Run focused seeder tests.

### Task 8: LLM-Assisted Candidate Annotation

**Files:**
- Create: `tools/seed/llm.py`
- Create: `docs/adrs/0011-llm-assisted-seed-annotation.md`
- Modify: `tools/seed/__main__.py`
- Modify: `scripts/seed-osv-overlays.sh`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/adrs/0010-overlay-taxonomies-and-seeding.md`
- Modify: `docs/adrs/INDEX.md`
- Test: `tests/test_seed_cli.py`
- Test: `tests/test_seed_llm.py`
- Test: `tests/test_seed_workflow_script.py`

- [x] Write tests proving `--llm-provider` receives the OSV record, framework documents, and neutral annotation schema.
- [x] Write tests proving invalid LLM output fails without writing a candidate.
- [x] Write tests proving LLM mode does not backfill missing annotation fields from deterministic heuristics.
- [x] Implement OpenAI and Anthropic provider adapters for LLM annotation.
- [x] Keep deterministic discovery and deterministic annotation as the no-LLM fallback path.
- [x] Add `OPENACA_LLM_PROVIDER` and `OPENACA_LLM_MODEL` support to the npm/PyPI workflow script.
- [x] Document usage in `CONTRIBUTING.md`.
- [x] Run focused seeder and workflow tests.
- [x] Run full verification before PR.
