# Plan 013 — Rename ASVE → OpenACA

**Goal:** Rename the project from ASVE to OpenACA across the schema namespace, CLI binaries, code, overlays, fixtures, docs, and ADRs. Delete vestigial `ASVE-NNNN-NNNN` identifier code and migrate test fixtures to upstream IDs (ADR-0009 overlay-only V0).

**Architecture:** This is a sweeping rename plus a small deletion. We are pre-V0 with no external consumers (saved memory: pre-V0 free-to-rename), so we change contracts in one shot rather than hedging with aliases. The CLI also collapses from per-tool entry points (`asve-scan`, `asve-lint`, ...) into a single `openaca` binary with subcommands (kubectl/gh pattern).

**Tech Stack:** Python 3.11, Click multi-command groups, JSON Schema, PyYAML, pytest, ruff, pyright.

---

### Task 1 — Schema file + namespace
- `git mv schema/asve.schema.json schema/openaca.schema.json`.
- `$id` → `https://openaca.dev/schema/openaca.schema.json`; `title` → `OpenACA Overlay`.
- `database_specific.properties.asve` → `openaca`; `$defs.asve_extension` → `openaca_extension`; `$defs.asve_taxonomies` → `openaca_taxonomies`; update internal `$ref` to match.

### Task 2 — Overlays + fixtures YAML namespace
- In every `overlays/*.yaml` and `tests/fixtures/**/*.yaml`, replace the YAML key `asve:` under `database_specific:` with `openaca:`.
- In invalid fixtures, replace `id: ASVE-2026-NNNN` with `id: CVE-2026-NNNN` (preserves digits).
- `git mv tests/fixtures/valid/asve-2026-0001.yaml tests/fixtures/valid/cve-2026-0001.yaml` and update its `id:` field.

### Task 3 — `tools/` sweep
- Bulk-substitute in all `tools/**/*.py` and `tools/templates/*`:
  - `database_specific.asve` → `database_specific.openaca` (Python dict accessors + Jinja paths).
  - Local var names `asve`, `ds_asve`, `asve_block` → `openaca`, `ds_openaca`, `openaca_block`.
  - `asve.dev` → `openaca.dev`.
  - `schema/asve.schema.json` path constants → `schema/openaca.schema.json`.
  - `ASVE` brand string → `OpenACA` in docstrings / help text / messages.

### Task 4 — Delete `ASVE-NNNN-NNNN` ID code
- In `tools/lint.py`: delete `ID_RE` and `check_internal_aliases`; drop the call site in `main()`; drop now-unused `known_ids` local.
- Update `tests/test_e2e.py` `test_real_corpus_lints_clean` to stop calling `check_internal_aliases` and stop computing `known_ids`.

### Task 5 — Migrate ASVE-2026-NNNN test refs to CVE-2026-NNNN
- Bulk-substitute `ASVE-(\d{4})-(\d{4})` → `CVE-\1-\2` across all test files and any docs that synthesize fixture IDs.
- Verify: `rg "ASVE-\d{4}"` returns nothing.

### Task 6 — Single `openaca` binary, subcommands
- New `tools/cli.py`: a top-level Click group that imports each tool's command (`scan`, `lint`, `export`, `promote`, `seed`) and registers them as subcommands.
- `pyproject.toml`: replace the five `asve-*` entries in `[project.scripts]` with `openaca = "tools.cli:main"`. Update `[project].name` to `openaca` and `[project].description`.
- `action.yml`: invoke `uv run openaca scan ...` and rename input default `asve-results.sarif` → `openaca-results.sarif`.
- `scripts/seed-osv-overlays.sh`: invoke `openaca seed`; rename envvar prefix `ASVE_*` → `OPENACA_*`; rename state filenames `.asve-seed-state-*.json` → `.openaca-seed-state-*.json`.
- `scripts/git-hooks/pre-push`: rename its temp log filenames.
- `CONTRIBUTING.md`: update all CLI examples.
- Update tests that subprocess the script (`tests/test_seed_workflow_script.py`) for the new state-file names.

### Task 7 — Docs sweep
- `git mv docs/specs/asve-thesis.md docs/specs/openaca-thesis.md`.
- `git mv docs/specs/asve-v0-design.md docs/specs/openaca-v0-design.md`.
- `git mv docs/adrs/0006-asve-scan-subcommands-and-attribution.md docs/adrs/0006-openaca-scan-subcommands-and-attribution.md`.
- Bulk substitute across `docs/`, `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE-DATA`:
  - `ASVE` → `OpenACA`; lowercase `asve` → `openaca` (variable/word boundary).
  - CLI command names `asve-scan` etc. → `openaca scan` etc.
  - `asve.dev` → `openaca.dev`.
  - `ASVE-(\d{4})-(\d{4})` → `CVE-\1-\2`.
- README.md: add a one-line handoff note: "Previously called ASVE; renamed May 2026 to reflect Agent Composition Analysis (ACA) category framing."
- `docs/adrs/INDEX.md`: update the renamed ADR-0006 path.
- `docs/plans/README.md`: add a row for plan 013.

### Task 8 — Root state files + package name
- `git mv .asve-seed-state-npm.json .openaca-seed-state-npm.json`.
- `git mv .asve-seed-state-pypi.json .openaca-seed-state-pypi.json`.
- Update the state-file path constant in `tools/seed/__main__.py`.

### Task 9 — Full gate
- `uv sync` (refresh lockfile for renamed package).
- `uv run ruff format --check tools/ tests/`.
- `uv run ruff check tools/ tests/`.
- `uv run pyright tools/ tests/`.
- `uv run pytest -q`.
- `uv run openaca lint overlays/`.

### Task 10 — Commit, push, PR
- 2–4 logical commits; push to `origin rename/openaca`; PR titled "Rename ASVE to OpenACA".
