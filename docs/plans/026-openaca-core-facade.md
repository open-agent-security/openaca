# OpenACA Core Facade Implementation Plan

**Goal:** Expose a curated `openaca.core` facade (per ADR-0028) so downstream
consumers depend on a pinned, supported domain surface instead of importing
`tools.*` internals or reimplementing identity / BOM / query-planning /
matching / severity semantics.

**Architecture:** `openaca/core/` is a thin re-export layer over the existing
`tools.*` modules — no logic moves. Two small additions make the surface
usable by a consumer that fetches advisories itself (e.g. an async consumer
such as the Fleet upload path): a public `bom_components_from_cyclonedx`
(reusing the existing `BOMComponent`) so consumers can map findings back to a
CycloneDX `bom-ref`, and a public `stamp_osv_query_provenance` extracted from
`augment_corpus` so a consumer's own fetch can stamp records with the same
`osv_query_matches` metadata `match()` relies on. The `tools/` → `openaca/*`
migration is explicitly out of scope (deferred behind the facade per ADR-0028).

**Tech stack:** Python, `uv`, ruff, pyright, pytest. Gate: `ruff format`,
`ruff check`, `pyright`, `pytest`, `openaca lint`.

Read first: ADR-0028 (`docs/adrs/0028-openaca-core-consumption-facade.md`),
ADR-0027 (query semantics), `tools/bom.py` (`BOMComponent`,
`component_refs_from_cyclonedx`), `tools/osv_federation.py` (`augment_corpus`,
`_record_matching_queries`, `_stamp_query_matches`, `collect_osv_queries`,
`OsvQuery`), `tools/matcher.py` (`match`, `Finding`), `tools/severity.py`.

---

## Task 1: `bom_components_from_cyclonedx` (bom-ref pairing)

**Files:** modify `tools/bom.py`; test `tests/test_bom.py`.

Consumers persist findings against their stored rows by CycloneDX `bom-ref`
(ADR-0028), but `component_refs_from_cyclonedx` returns bare `ComponentRef`s.
Reuse the existing `BOMComponent(ref, bom_ref)` type rather than inventing a
tuple pairing.

- [x] **Write the failing test.** A CycloneDX doc with two components carrying
  `bom-ref` values round-trips through `bom_components_from_cyclonedx(doc)` to a
  `list[BOMComponent]` where each `.bom_ref` equals the source component's
  `bom-ref` and `.ref` equals what `component_refs_from_cyclonedx` produced for
  it (same order).
- [x] **Run it; confirm it fails** (`bom_components_from_cyclonedx` undefined).
- [x] **Implement.** Factor the per-component reconstruction loop in
  `component_refs_from_cyclonedx` into `bom_components_from_cyclonedx(doc) ->
  list[BOMComponent]`, pairing each reconstructed `ComponentRef` with the
  component's `bom-ref` (fall back to a stable synthesized ref only if absent,
  mirroring `_stable_bom_refs` semantics if needed). Reimplement
  `component_refs_from_cyclonedx(doc)` as
  `[c.ref for c in bom_components_from_cyclonedx(doc)]`.
- [x] **Run tests; confirm pass.** The existing `component_refs_from_cyclonedx`
  tests must remain green (behavior-preserving delegation).
- [x] **Commit.**

## Task 2: `stamp_osv_query_provenance` (public provenance helper)

**Files:** modify `tools/osv_federation.py`; test `tests/test_osv_federation.py`.

A consumer that fetches advisories with its own client must stamp records with
the query provenance `match()` trusts (`osv_query_matches`). That logic lives
privately inside `augment_corpus`'s loop today.

- [x] **Write the failing test.** Given a fetched OSV record with a `GIT` range
  for `github.com/o/r` and a `git_version` `OsvQuery` for that repo/ref,
  `stamp_osv_query_provenance(record, [query])` returns `True` and the record
  gains the `database_specific.openaca.osv_query_matches` entry. Given a query
  whose git repo does not match the record, it returns `False` and stamps
  nothing.
- [x] **Run it; confirm it fails.**
- [x] **Implement.** Add `stamp_osv_query_provenance(record, queries) -> bool`
  wrapping the existing `_record_matching_queries` + `_stamp_query_matches`:
  filter to matching queries, stamp, return whether any matched. Refactor
  `augment_corpus`'s inline loop to call it (single source of truth).
  Docstring MUST state the contract: `queries` is **only** the queries that
  returned this record (e.g. `matches_by_id[vid]`), never all scan queries —
  otherwise non-git PURL queries always "match" and a different advisory's
  git query could be stamped onto the wrong record.
- [x] **Run tests; confirm pass.** All existing federation tests stay green
  (proves the extraction is behavior-preserving).
- [x] **Commit.**

## Task 3: `openaca/core/` facade package + packaging

**Files:** create `openaca/core/` submodules; modify `pyproject.toml`; test
`tests/test_core_facade.py`.

- [x] **Write the failing test.** Import each facade symbol from `openaca.core`
  and assert it is the *same object* as the underlying `tools.*` symbol
  (re-export identity), for the full surface below.
- [x] **Run it; confirm it fails** (`openaca.core` does not exist).
- [x] **Implement the curated facade** as named re-exports (not wildcard),
  grouped into submodules:
  - `openaca/core/component_ref.py`: `ComponentRef`
  - `openaca/core/bom.py`: `BOMComponent`, `build_agent_bom`,
    `component_refs_from_cyclonedx`, `bom_components_from_cyclonedx`
  - `openaca/core/osv_queries.py`: `OsvQuery`, `collect_osv_queries`,
    `stamp_osv_query_provenance`
  - `openaca/core/matching.py`: `Finding`, `match`
  - `openaca/core/severity.py`: `derive_severity_label`, `derive_severity_score`
  - `openaca/core/__init__.py`: re-export the above for `from openaca.core import ...`
- [x] **Update packaging.** Add the new `openaca` package to `pyproject.toml`
  build config so `openaca.core` ships when the package is installed/pinned.
- [x] **Run tests; confirm pass.**
- [x] **Commit.**

## Verification

- [x] `uv run ruff format --check . && uv run ruff check . && uv run pyright`
- [x] `uv run pytest -q` (federation tests green = `augment_corpus` unchanged in
  behavior; facade identity tests pass)
- [x] `uv run openaca lint overlays/`
- [x] In a throwaway venv, `pip install`/build the package and confirm
  `from openaca.core import match, collect_osv_queries, stamp_osv_query_provenance,
  bom_components_from_cyclonedx` resolves (packaging actually ships the facade).

## Deferred (not in this plan)

- `tools/` → `openaca/*` namespace migration (absorbed behind the facade later,
  per ADR-0028).
- Renderer attribution extraction: `_containment_marker` / `_bundled_finding_ids`
  are presentation, not domain. The reusable datum is `Finding.attributed_to`;
  the "component X bundles vulnerable Y" display belongs to each consumer.
- Downstream consumer changes (separate repo): pin OpenACA, reconstruct refs via
  `bom_components_from_cyclonedx`, plan with `collect_osv_queries`, fetch with the
  consumer's own client, stamp via `stamp_osv_query_provenance`, match via
  `match`, join findings by `bom-ref`, derive severity via the facade helpers.
- A consumer-side contract test (fixture BOM with npm + GitHub commit + Git tag
  + Docker) asserting parity with CLI semantics and Docker-skip.
