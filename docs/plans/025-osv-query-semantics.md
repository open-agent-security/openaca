# OSV Query Semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** OpenACA sends OSV.dev queries using the query shape OSV actually
supports: package PURLs for npm/PyPI, commit queries for immutable Git refs,
and GIT package/version queries for Git refs.

**Architecture:** Replace the PURL-only federation target list with typed OSV
query objects. `ComponentRef.purl` remains the internal/BOM source identity,
but `tools.osv_federation` decides whether a ref has a supported OSV query
shape. Parser metadata preserves mutable Git refs so federation can query OSV's
GIT version path without encoding those refs as PURL versions.

**Tech Stack:** Python stdlib dataclasses/json/urllib, pytest tests in
`tests/test_osv_federation.py`, parser tests in
`tests/test_parsers/test_mcp_json.py`, verbose-output tests in
`tests/test_scan.py`.

## Tasks

- [x] **Task 1: Preserve mutable Git refs as scanner metadata**
  - Modify `tools/parsers/mcp_json.py`.
  - Add a failing parser test asserting `uvx --from git+https://github.com/o/r@v1.0.0`
    produces `version is None` and `extra["git_ref"] == "v1.0.0"`.
  - Implement by threading the raw ref separately from the immutable commit SHA.

- [x] **Task 2: Replace PURL-only federation targets with OSV query objects**
  - Modify `tools/osv_federation.py`.
  - Add tests for:
    - npm/PyPI refs become package PURL queries.
    - GitHub commit refs become `{"commit": "<sha>"}` queries.
    - GitHub mutable refs become `{"version": "<ref>", "package":
      {"ecosystem": "GIT", "name": "github.com/o/r"}}` queries.
    - Generic Docker refs are skipped.
  - Keep query deduplication stable by query key.

- [x] **Task 3: Fetch, filter, and match federated Git records**
  - Modify `tools/osv_federation.py`.
  - Change `/v1/querybatch` handling to retain which query returned each ID.
  - Filter Git query results to records with a matching GIT range repo so a
    bare commit collision or unrelated result cannot create a finding.
  - Modify `tools/matcher.py` so fetched GIT records produce findings for
    commit refs and affected tag/ref versions.

- [x] **Task 4: Update verbose federation output**
  - Modify `tools/scan.py`.
  - Replace "queried PURL(s)" language with "queried target(s)".
  - Include readable target labels for package PURLs, Git commits, and Git
    refs. Keep skipped refs bucketed by ecosystem/component type.

- [x] **Task 5: Verify and finish**
  - Run focused tests:
    - `uv run pytest tests/test_parsers/test_mcp_json.py tests/test_osv_federation.py tests/test_scan.py -k "github_url_mutable_ref or osv or federation"`
  - Run full gates:
    - `uv run pytest`
    - `uv run ruff check .`
    - `uv run ruff format --check .`
    - `uv run pyright`
  - Commit, push, and open a PR.
