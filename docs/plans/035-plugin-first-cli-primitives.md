# Plugin-First CLI Primitives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local `openaca bom diff` primitive for plugin-first "what changed?" workflows.

**Architecture:** Implement a small pure diff module over CycloneDX Agent BOM JSON, then expose it through `tools/bom_cli.py`. Diff identity is `bom-ref` and output includes both components and composition edges.

**Tech Stack:** Python 3.11, Click, pytest, existing CycloneDX Agent BOM shape.

---

### Task 1: Pure BOM Diff Model

**Files:**
- Create: `tools/bom_diff.py`
- Create: `tests/test_bom_diff.py`

- [x] Write tests for added, removed, changed components and edge changes.
- [x] Implement pure parsing and diff functions.
- [x] Run `uv run pytest tests/test_bom_diff.py -q`.

### Task 2: CLI Command

**Files:**
- Modify: `tools/bom_cli.py`
- Modify: `tests/test_bom_cli.py`

- [x] Write CLI tests for text and JSON output.
- [x] Add `openaca bom diff --before <file> --after <file> [--format text|json]`.
- [x] Run `uv run pytest tests/test_bom_diff.py tests/test_bom_cli.py -q`.

### Task 3: Docs And Verification

**Files:**
- Modify: `docs/reference/cli.md`

- [x] Document `openaca bom diff` as a local, no-advisory-lookup command.
- [x] Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pyright`, and focused pytest.
- [ ] Commit, push, and open a ready PR.
