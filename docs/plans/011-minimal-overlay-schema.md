# Plan 011 — Minimal Overlay Schema

**Goal:** Make canonical ASVE overlays a minimal, standards-based enrichment layer over upstream OSV records.

**Architecture:** Canonical overlays keep only `database_specific.asve.taxonomies`, `evidence_level`, and optional `threat_kind`. Scanner-observed component context remains in scan output, while candidates keep review-only LLM evidence and rejection metadata outside the canonical projection.

**Tech Stack:** Python 3.11, JSON Schema, PyYAML, Click, pytest, existing ASVE scanner/seed/promote/render tooling.

---

### Task 1: Schema Contract Tests

**Files:**
- Modify: `tests/test_schema.py`
- Modify: `tests/test_seed_validator.py`
- Modify: `tests/test_seed_llm.py`

- [x] Add a schema test that accepts a canonical overlay with only `taxonomies` and `evidence_level` under `database_specific.asve`.
- [x] Add schema tests that reject canonical `component_identity`, `component_type`, `surfaces`, and `agent_impact` under `database_specific.asve`.
- [x] Add a schema test that accepts `threat_kind: malicious_package` and rejects any other `threat_kind`.
- [x] Update seed-validator tests so valid candidates project to the minimal canonical overlay shape.
- [x] Update LLM-schema tests so `load_annotation_schema()` exposes the minimal ASVE schema and no longer advertises component classification fields.
- [x] Run `uv run pytest tests/test_schema.py tests/test_seed_validator.py tests/test_seed_llm.py -q` and confirm the new tests fail for the current schema.

### Task 2: Canonical Schema And Overlay Migration

**Files:**
- Modify: `schema/asve.schema.json`
- Modify: `overlays/*.yaml`
- Modify: `tests/fixtures/valid/asve-2026-0001.yaml`
- Modify: `tests/fixtures/invalid/bad-cvss.yaml`
- Modify: `tests/fixtures/invalid/bad-datetime.yaml`

- [x] Change `$defs.asve_extension.required` from `["component_type"]` to `["taxonomies", "evidence_level"]`.
- [x] Remove `component_identity`, `component_type`, `surfaces`, and `agent_impact` from canonical `$defs.asve_extension.properties`.
- [x] Change `threat_kind` from free-form string to enum `["malicious_package"]`.
- [x] Set `additionalProperties: false` on `$defs.asve_extension`.
- [x] Remove `component_type`, `surfaces`, `agent_impact`, and V0-unused `detection_hints` from bundled overlays.
- [x] Update schema fixtures to the minimal ASVE block.
- [x] Run `uv run pytest tests/test_schema.py tests/test_overlays.py tests/test_lint.py -q`.
- [x] Run `uv run asve-lint overlays/`.

### Task 3: Seeder, LLM, And Rejection Artifacts

**Files:**
- Modify: `tools/seed/__main__.py`
- Modify: `tools/seed/llm.py`
- Modify: `tools/seed/validator.py`
- Modify: `tests/test_seed_cli.py`
- Modify: `tests/test_seed_llm.py`

- [x] Update deterministic candidate annotation to emit only `taxonomies`, `evidence_level`, and `threat_kind: malicious_package` for MAL records.
- [x] Update the LLM request/response path so an annotation response can be either `decision: annotate` with a minimal ASVE annotation, or `decision: reject` with `reject_reason` and evidence.
- [x] Define reject reasons as a closed set: `not_agent_stack`, `insufficient_evidence`, `duplicate_scope`, and `unsupported_record`.
- [x] Write rejected LLM decisions to `candidates/rejected/<id>.yaml` instead of silently skipping them.
- [x] Ensure state advances only after either an annotation candidate or a rejected candidate artifact is written.
- [x] Update candidate validation to reject canonical-only fields that no longer exist and to validate the projected minimal overlay.
- [x] Run `uv run pytest tests/test_seed_cli.py tests/test_seed_llm.py tests/test_seed_validator.py -q`.

### Task 4: Promotion And Rendering

**Files:**
- Modify: `tools/promote.py`
- Modify: `tools/render.py`
- Modify: `tools/templates/advisory.html.j2`
- Modify: `tools/templates/index.html.j2`
- Modify: `tests/test_promote.py`
- Modify: `tests/test_render.py`
- Modify: `tests/test_export.py`
- Modify: `tests/test_scan.py`

- [x] Update promotion tests so promoted overlays strip candidate-only fields and retain only the minimal ASVE block.
- [x] Ensure promoted overlays reject `component_identity`, `component_type`, `surfaces`, and `agent_impact` in the candidate ASVE block.
- [x] Remove verbose text rendering for overlay `surfaces` and `agent_impact`.
- [x] Update static advisory and index templates to render taxonomy and evidence-level information without a component-type column/search dependency.
- [x] Update scan/render/export tests that asserted component type, surfaces, or agent impact output.
- [x] Run `uv run pytest tests/test_promote.py tests/test_render.py tests/test_export.py tests/test_scan.py -q`.

### Task 5: Non-Package Component Identity Cleanup

**Files:**
- Create: `docs/adrs/0013-non-package-component-identities.md`
- Modify: `docs/adrs/INDEX.md`
- Modify: `tools/parsers/hooks_json.py`
- Modify: `tools/render.py`
- Modify: `tests/test_parsers/test_hooks_json.py`
- Modify: `tests/test_parsers/test_claude_install.py`
- Modify: `tests/test_render.py`

- [x] Add ADR-0013 documenting that `component_identity` is logical identity and observation location belongs in `source_manifest`, `source_locator`, `attributed_to`, and `extra`.
- [x] Update hook parser tests so settings-scoped and plugin-bundled hooks with the same hook payload emit the same logical `component_identity`.
- [x] Update hook parser implementation to build identity from hook type and payload, not settings scope, event, or index.
- [x] Preserve hook observation metadata in `extra`: `scope`, `event`, `index`, `type`, `command`, and `matcher`.
- [x] Update endpoint/render tests that previously expected slot-shaped hook identities.
- [x] Run `uv run pytest tests/test_parsers/test_hooks_json.py tests/test_parsers/test_claude_install.py tests/test_render.py -q`.

### Task 6: Documentation And Final Verification

**Files:**
- Modify: `CONTRIBUTING.md`
- Modify: `docs/plans/010-seed-overlay-pipeline.md` only if stale references block understanding.
- Modify: `docs/plans/README.md`

- [x] Update contributor seeding guidance to describe minimal canonical overlays and candidate-only evidence/rejection metadata.
- [x] Update any stale seeding workflow text that still describes canonical `component_type`, `surfaces`, or `agent_impact`.
- [x] Update `docs/plans/README.md` so this plan is active while implementation is in progress.
- [x] Run `uv run pytest -q`.
- [x] Run `uv run ruff check .`.
- [x] Run `uv run ruff format --check .`.
- [x] Run `uv run pyright`.
- [x] Run `uv run asve-lint overlays/`.
- [x] Run `git diff --check`.
- [x] Review the diff for accidental changes to generated `candidates/` or main-worktree artifacts.
- [x] Commit, push, and open a PR after user approval.
