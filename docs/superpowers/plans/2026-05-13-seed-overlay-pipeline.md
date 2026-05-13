# Seed Overlay Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a V0-safe deterministic workflow for seeding reviewed ASVE overlays from OSV bulk dumps.

**Architecture:** Canonical `overlays/` stays scanner-visible and minimal. The seeder writes heuristic candidates to `candidates/`, outside the scanner/export path. A human reviews and edits each candidate, then `asve-promote` projects it into the canonical overlay shape and validates it before writing `overlays/<id>.yaml`.

**Tech Stack:** Python 3.11, Click CLIs, PyYAML, JSON Schema, pytest, existing ASVE schema/linter/export tooling.

---

### Task 1: ADR And Overlay Taxonomy Schema

**Files:**
- Create: `docs/adrs/0010-overlay-taxonomies-and-seeding.md`
- Modify: `docs/adrs/INDEX.md`
- Modify: `schema/asve.schema.json`
- Modify: `overlays/*.yaml`
- Modify: `tools/templates/advisory.html.j2`
- Test: `tests/test_schema.py`

- [ ] Write tests that require `database_specific.asve.taxonomies` and allow `threat_kind`.
- [ ] Run focused schema tests and confirm they fail before schema changes.
- [ ] Update schema to add `taxonomies` and `threat_kind`.
- [ ] Migrate existing overlays from flat `owasp_agentic_top10` to `taxonomies.owasp_agentic_top10`.
- [ ] Update export HTML template to render taxonomy groups.
- [ ] Add ADR-0010 documenting taxonomy shape, candidate-vs-overlay boundary, no LLM in V0, and no CWE duplication by default.
- [ ] Run `uv run asve-lint overlays/` and focused schema/export tests.

### Task 2: Promotion Boundary

**Files:**
- Create: `tools/promote.py`
- Modify: `pyproject.toml`
- Test: `tests/test_promote.py`

- [ ] Write tests showing `asve-promote candidates/GHSA-x.yaml` strips `_candidate`, evidence, upstream summaries/details, and other candidate-only fields.
- [ ] Run focused promotion tests and confirm they fail.
- [ ] Implement `project_candidate_to_overlay()` and Click CLI.
- [ ] Validate promoted overlays with the canonical JSON schema.
- [ ] Register `asve-promote` in `pyproject.toml`.
- [ ] Run focused promotion tests.

### Task 3: Candidate Validator

**Files:**
- Create: `tools/seed/__init__.py`
- Create: `tools/seed/validator.py`
- Test: `tests/test_seed_validator.py`

- [ ] Write validator tests for valid candidates, invalid taxonomy IDs, invalid impact shapes, missing candidate review metadata, and upstream-owned canonical fields that promotion must drop.
- [ ] Run focused validator tests and confirm they fail.
- [ ] Implement deterministic candidate validation helpers that reuse the canonical schema on the projected overlay.
- [ ] Run focused validator tests.

### Task 4: Deterministic Seeder CLI

**Files:**
- Create: `tools/seed/__main__.py`
- Modify: `pyproject.toml`
- Test: `tests/test_seed_cli.py`

- [ ] Write CLI tests for deterministic discovery, candidate output, curated overlay dedup, MAL record handling, and dry-run output.
- [ ] Run focused seeder tests and confirm they fail.
- [ ] Implement OSV dump iteration, MCP/agent discovery heuristics, rule-based draft annotations, candidate validation, and `candidates/` output.
- [ ] Register `asve-seed` in `pyproject.toml`.
- [ ] Run focused seeder tests.

### Task 5: Full Verification And PR

**Files:**
- Modify as needed based on test fallout only.

- [ ] Run `uv run pytest -q`.
- [ ] Run `uv run ruff check .`.
- [ ] Run `uv run ruff format --check .`.
- [ ] Run `uv run pyright`.
- [ ] Run `uv run asve-lint overlays/`.
- [ ] Run `git diff --check`.
- [ ] Review diff for accidental main-worktree spike carryover or canonical overlay noise.
- [ ] Commit, push, and open a ready PR.
