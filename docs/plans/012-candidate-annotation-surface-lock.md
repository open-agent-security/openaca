# Candidate Annotation Surface Lock + Review Skill Template

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Constrain the LLM/agent-editable surface of OpenACA candidates to `taxonomies` + `evidence_level` (everything else seeder-owned or upstream-owned), enforce that boundary in CI via the validator and overlay linter, and ship a thin Claude Code skill template so local subscription-based annotation has a documented UX.

**Architecture:** Two-phase OpenACA annotation. The deterministic seeder (`openaca seed`) owns discovery, state, dedup, and the small set of mechanical fields (incl. `threat_kind` for MAL-* records). Semantic judgment (taxonomy mappings, evidence level) moves out of the API LLM path into a Claude Code skill that runs in the user's authenticated session — subscription quota, not API keys. Both the candidate validator and the overlay linter enforce the editable-surface contract structurally; semantic rules live in `docs/seed-review-rules.md` for human/agent reference.

**Tech Stack:** Python 3.11, jsonschema (Draft202012Validator), click CLI, YAML candidate/overlay files, Markdown skill template.

**Context:** Follows PR #43 (minimal overlay schema, ADR-0012) and PR #44 (LLM provider output schema binding). Codex hallucinated `threat_kind: malicious_package` for the Flowise GHSA-mq53-pc65-wjc4 IDOR — that misclassification is the canonical regression test for this PR.

---

## Task 1: Add canonical review rules document

**Files:**
- Create: `docs/seed-review-rules.md`

- [x] **Step 1: Write the rules document**

Create `docs/seed-review-rules.md` with the structural-vs-semantic split:

````markdown
# OpenACA candidate annotation rules

Canonical rules for annotating reviewable seed candidates under
`candidates/` and the canonical overlays under `overlays/`. Both the
deterministic seeder, the LLM annotator (when used), and the
candidate-review skill (when used) MUST conform.

This document is the single source of truth for review behavior. It is
read by `tools/seed/validator.py` and `tools/lint.py` (which enforce
the structural subset in CI), by reviewers, and by the Claude Code
skill at `examples/skills/claude/openaca-candidate-review/SKILL.md`.

## Editable fields

When annotating or re-reviewing a candidate, ONLY edit:

- `database_specific.openaca.taxonomies.*` (framework mapping arrays)
- `database_specific.openaca.evidence_level` (one of:
  `confirmed`, `likely`, `research`, `disputed`, `withdrawn`)

## Forbidden fields (do not set, edit, or remove via review)

- `database_specific.openaca.threat_kind` — seeder-owned (see below)
- `_candidate.*`, `_evidence.*` — review metadata, set by the seeder
- `id`, `aliases`, `modified`, `summary`, `details`, `references`,
  `affected`, `severity` — upstream-owned, sourced from the OSV record

## Deterministic fields owned by the seeder

`threat_kind` is allowed only when the record's `id` starts with `MAL-`
OR `aliases` contains a `MAL-*` entry. In that case `threat_kind`
MUST be `"malicious_package"`. For all other records (ordinary CVEs,
GHSAs, authz bugs, code execution flaws in agent-stack packages),
`threat_kind` MUST be omitted entirely.

Rationale: per ADR-0012, `threat_kind` flags genuinely malicious or
backdoored packages. Ordinary vulnerabilities in agent-stack packages
are vulnerabilities, not malware; setting `threat_kind` for them
misroutes downstream consumers.

## Structural rules (CI-enforced)

The validator (`tools/seed/validator.py`) and overlay linter
(`tools/lint.py`) reject:

1. `threat_kind` set when neither `id` nor any `aliases[]` entry
   begins with `MAL-`.
2. Empty taxonomy arrays (e.g., `owasp_mcp_top10: []`). Omit the key
   instead.
3. Empty `supplemental_taxonomies: {}`. Omit the key instead.
4. Unknown keys under `database_specific.openaca` — schema enforces
   `additionalProperties: false` and rejects via JSON-schema
   validation.

## Semantic rules (review-enforced)

These cannot be checked structurally and depend on reading the OSV
record. The skill and human reviewers MUST apply them:

1. **No supply-chain mapping without supply-chain evidence.** Do not
   set `owasp_agentic_top10: [asi04]`, `owasp_llm_top10: [llm03:2025]`,
   or `mitre_atlas: [AML.T0010.*]` unless the OSV record describes a
   malicious package, compromised dependency, typosquat, or
   dependency-confusion attack. A code bug in an agent-stack package
   is not a supply-chain incident.
2. **Prefer omission over speculation.** If a framework family does
   not apply, omit the key entirely. Do not emit empty arrays or
   guess-quality mappings.
3. **Quote your evidence.** Each taxonomy assertion should be
   defensible against a quoted line from the OSV `summary`/`details`.
4. **Evidence level discipline.** Use `confirmed` only when the OSV
   record itself states the agent context. Use `likely` when the
   package or component is clearly agent-stack and the OSV record
   supports the classification. Use `research` when the mapping is
   plausible but needs reviewer confirmation.

## Review modes

The Claude Code skill supports two modes:

- **Annotate**: candidate has no `database_specific.openaca` block.
  Skill adds it from scratch, applying both structural and semantic
  rules.
- **Re-review**: candidate has an existing `database_specific.openaca`
  block (typically from an earlier API-mode annotation). Skill
  audits it against the rules above, edits to comply, and re-runs
  validation.

## Worked example: Flowise GHSA-mq53-pc65-wjc4

The OSV record describes an Object.assign mass-assignment / IDOR bug
in Flowise's evaluation controller. An authenticated workspace member
can overwrite `workspaceId` on a row they own, exposing captured
prompts and model outputs to other workspaces.

**Correct annotation:**

```yaml
database_specific:
  openaca:
    taxonomies:
      owasp_agentic_top10: [asi03]    # identity / privilege abuse
      owasp_llm_top10: [llm02:2025]   # sensitive info disclosure
    evidence_level: likely
```

**Wrong annotation (nano-style):**

```yaml
database_specific:
  openaca:
    threat_kind: malicious_package    # ← Flowise is not malicious
    taxonomies:
      owasp_agentic_top10: [asi04]    # ← not a supply-chain incident
      owasp_llm_top10: [llm03:2025]   # ← not supply chain
      mitre_atlas: [AML.T0010.001]    # ← not ATLAS supply chain
      owasp_agentic_skills_top10: []  # ← empty bucket, omit instead
      owasp_mcp_top10: []             # ← empty bucket, omit instead
      supplemental_taxonomies: {}     # ← empty, omit instead
    evidence_level: likely
```
````

- [x] **Step 2: Commit the rules document**

```bash
cd /Users/vinodkone/workspace/openaca/.worktrees/candidate-annotation-surface
git add docs/seed-review-rules.md
git commit -m "Add canonical seed-review rules document"
```

---

## Task 2: Validator — reject threat_kind on non-MAL records

**Files:**
- Modify: `tools/seed/validator.py`
- Test: `tests/test_seed_validator.py`

- [x] **Step 1: Write the failing test**

Append to `tests/test_seed_validator.py`:

```python
def test_validate_candidate_rejects_threat_kind_on_non_mal_record():
    """threat_kind is seeder-owned and only valid on MAL-* ids/aliases."""
    candidate = _candidate()
    candidate["database_specific"]["openaca"]["threat_kind"] = "malicious_package"

    errors = validate_candidate(candidate)

    assert any(
        "threat_kind" in e and "MAL-" in e for e in errors
    ), f"expected actionable threat_kind error, got: {errors}"


def test_validate_candidate_accepts_threat_kind_on_mal_record_id():
    candidate = _candidate()
    candidate["id"] = "MAL-2026-0001"
    candidate["database_specific"]["openaca"]["threat_kind"] = "malicious_package"

    assert validate_candidate(candidate) == []


def test_validate_candidate_accepts_threat_kind_on_mal_alias():
    candidate = _candidate()
    candidate["aliases"] = ["MAL-2026-0042"]
    candidate["database_specific"]["openaca"]["threat_kind"] = "malicious_package"

    assert validate_candidate(candidate) == []
```

- [x] **Step 2: Run tests to verify they fail**

```bash
cd /Users/vinodkone/workspace/openaca/.worktrees/candidate-annotation-surface
uv run pytest tests/test_seed_validator.py::test_validate_candidate_rejects_threat_kind_on_non_mal_record tests/test_seed_validator.py::test_validate_candidate_accepts_threat_kind_on_mal_record_id tests/test_seed_validator.py::test_validate_candidate_accepts_threat_kind_on_mal_alias -v
```

Expected: 1 fail (`threat_kind_on_non_mal_record` — accepts because schema allows it), 2 may pass already (no threat_kind on _candidate fixture).

- [x] **Step 3: Implement the rule in validator.py**

Add a helper after `validate_candidate` in `tools/seed/validator.py`:

```python
def _check_threat_kind_id_coupling(candidate: dict[str, Any]) -> list[str]:
    """threat_kind is only valid when id or an alias starts with MAL-."""
    openaca = (candidate.get("database_specific") or {}).get("openaca") or {}
    if "threat_kind" not in openaca:
        return []
    record_id = candidate.get("id") or ""
    aliases = candidate.get("aliases") or []
    if isinstance(record_id, str) and record_id.startswith("MAL-"):
        return []
    if any(isinstance(a, str) and a.startswith("MAL-") for a in aliases):
        return []
    return [
        f"threat_kind set on non-MAL record {record_id or '<unknown id>'}; "
        "threat_kind is only valid on MAL-* ids or aliases"
    ]
```

Then call it from `validate_candidate` before the `Draft202012Validator` loop returns:

```python
def validate_candidate(candidate: dict[str, Any]) -> list[str]:
    """Return structural validation errors for a seed candidate."""
    errors: list[str] = []
    metadata = candidate.get("_candidate")
    if not isinstance(metadata, dict):
        errors.append("_candidate: required review metadata block is missing")
    elif not metadata.get("matched_by"):
        errors.append("_candidate.matched_by: must list at least one discovery heuristic")

    try:
        overlay = project_candidate_to_overlay(candidate)
    except ValueError as exc:
        errors.append(str(exc))
        return errors

    errors.extend(_check_threat_kind_id_coupling(candidate))

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(overlay):
        path = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"schema: {error.message} (at {path})")
    return errors
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_seed_validator.py -v
```

Expected: all tests pass (including the three new ones).

- [x] **Step 5: Commit**

```bash
git add tools/seed/validator.py tests/test_seed_validator.py
git commit -m "Reject threat_kind on non-MAL candidates in validator"
```

---

## Task 3: Validator — reject empty taxonomy buckets

**Files:**
- Modify: `tools/seed/validator.py`
- Test: `tests/test_seed_validator.py`

- [x] **Step 1: Write the failing tests**

Append to `tests/test_seed_validator.py`:

```python
def test_validate_candidate_rejects_empty_taxonomy_array():
    candidate = _candidate()
    candidate["database_specific"]["openaca"]["taxonomies"]["owasp_mcp_top10"] = []

    errors = validate_candidate(candidate)

    assert any(
        "empty taxonomy bucket" in e and "owasp_mcp_top10" in e for e in errors
    ), f"expected empty-bucket error naming owasp_mcp_top10, got: {errors}"


def test_validate_candidate_rejects_empty_supplemental_taxonomies():
    candidate = _candidate()
    candidate["database_specific"]["openaca"]["taxonomies"]["supplemental_taxonomies"] = {}

    errors = validate_candidate(candidate)

    assert any(
        "empty taxonomy bucket" in e and "supplemental_taxonomies" in e for e in errors
    ), f"expected empty-bucket error naming supplemental_taxonomies, got: {errors}"


def test_validate_candidate_accepts_non_empty_taxonomies():
    """Sanity: existing _candidate fixture with one populated bucket still passes."""
    assert validate_candidate(_candidate()) == []
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_seed_validator.py::test_validate_candidate_rejects_empty_taxonomy_array tests/test_seed_validator.py::test_validate_candidate_rejects_empty_supplemental_taxonomies -v
```

Expected: both fail.

- [x] **Step 3: Implement the rule in validator.py**

Add a helper to `tools/seed/validator.py`:

```python
def _check_no_empty_taxonomy_buckets(candidate: dict[str, Any]) -> list[str]:
    """Reject empty arrays/dicts under taxonomies; omit the key instead."""
    openaca = (candidate.get("database_specific") or {}).get("openaca") or {}
    taxonomies = openaca.get("taxonomies")
    if not isinstance(taxonomies, dict):
        return []
    errors: list[str] = []
    for key, value in taxonomies.items():
        if isinstance(value, (list, dict)) and len(value) == 0:
            errors.append(
                f"empty taxonomy bucket {key!r}; omit the key instead of "
                f"emitting an empty {'array' if isinstance(value, list) else 'object'}"
            )
    return errors
```

Wire it into `validate_candidate` alongside the threat_kind check:

```python
    errors.extend(_check_threat_kind_id_coupling(candidate))
    errors.extend(_check_no_empty_taxonomy_buckets(candidate))
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_seed_validator.py -v
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```bash
git add tools/seed/validator.py tests/test_seed_validator.py
git commit -m "Reject empty taxonomy buckets in validator"
```

---

## Task 4: Overlay linter — same two rules at promotion gate

**Files:**
- Modify: `tools/lint.py`
- Test: `tests/test_lint.py`

The candidate validator catches issues at `openaca seed` time; the overlay linter (`openaca lint`) catches them at promotion time. Same rules, applied to the canonical overlay shape.

- [x] **Step 1: Inspect the existing overlay lint structure**

```bash
cd /Users/vinodkone/workspace/openaca/.worktrees/candidate-annotation-surface
grep -n "^def check_" tools/lint.py
grep -n "def test_" tests/test_lint.py | head -10
```

Note the existing `check_*` function pattern and the `main(target)` entry point that aggregates them.

- [x] **Step 2: Write the failing tests**

Append to `tests/test_lint.py` (use the existing fixture style — look at adjacent tests for the overlay shape and how `main` is invoked):

```python
def test_lint_rejects_threat_kind_on_non_mal_overlay(tmp_path, capsys):
    overlay_dir = tmp_path / "overlays"
    overlay_dir.mkdir()
    (overlay_dir / "GHSA-test-1234-5678.yaml").write_text(
        "schema_version: 1.7.5\n"
        "id: GHSA-test-1234-5678\n"
        "modified: '2026-05-14T00:00:00Z'\n"
        "database_specific:\n"
        "  openaca:\n"
        "    threat_kind: malicious_package\n"
        "    taxonomies:\n"
        "      owasp_agentic_top10: [asi05]\n"
        "    evidence_level: likely\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        lint.main.callback(target=overlay_dir)
    out = capsys.readouterr().err + capsys.readouterr().out
    assert exc.value.code != 0
    # Re-capture (callback already drained); the test really just asserts
    # nonzero exit. Error message is asserted in the validator unit tests.


def test_lint_rejects_empty_taxonomy_bucket_in_overlay(tmp_path):
    overlay_dir = tmp_path / "overlays"
    overlay_dir.mkdir()
    (overlay_dir / "GHSA-test-empty-bucket.yaml").write_text(
        "schema_version: 1.7.5\n"
        "id: GHSA-test-empty-bucket\n"
        "modified: '2026-05-14T00:00:00Z'\n"
        "database_specific:\n"
        "  openaca:\n"
        "    taxonomies:\n"
        "      owasp_agentic_top10: [asi05]\n"
        "      owasp_mcp_top10: []\n"
        "    evidence_level: likely\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        lint.main.callback(target=overlay_dir)
    assert exc.value.code != 0
```

(Add `import pytest` and `from tools import lint` if missing.)

- [x] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_lint.py::test_lint_rejects_threat_kind_on_non_mal_overlay tests/test_lint.py::test_lint_rejects_empty_taxonomy_bucket_in_overlay -v
```

Expected: both fail (lint accepts these today).

- [x] **Step 4: Add the two checks to lint.py**

Add to `tools/lint.py` next to the other `check_*` functions:

```python
def check_threat_kind_id_coupling(overlay: dict, path: Path) -> list[str]:
    """threat_kind valid only on MAL-* ids/aliases (mirrors validator.py)."""
    openaca = (overlay.get("database_specific") or {}).get("openaca") or {}
    if "threat_kind" not in openaca:
        return []
    record_id = overlay.get("id") or ""
    aliases = overlay.get("aliases") or []
    if isinstance(record_id, str) and record_id.startswith("MAL-"):
        return []
    if any(isinstance(a, str) and a.startswith("MAL-") for a in aliases):
        return []
    return [
        f"{path}: threat_kind set on non-MAL record {record_id or '<unknown id>'}; "
        "threat_kind is only valid on MAL-* ids or aliases"
    ]


def check_no_empty_taxonomy_buckets(overlay: dict, path: Path) -> list[str]:
    """Reject empty arrays/dicts under taxonomies (mirrors validator.py)."""
    openaca = (overlay.get("database_specific") or {}).get("openaca") or {}
    taxonomies = openaca.get("taxonomies")
    if not isinstance(taxonomies, dict):
        return []
    errors: list[str] = []
    for key, value in taxonomies.items():
        if isinstance(value, (list, dict)) and len(value) == 0:
            kind = "array" if isinstance(value, list) else "object"
            errors.append(
                f"{path}: empty taxonomy bucket {key!r}; "
                f"omit the key instead of emitting an empty {kind}"
            )
    return errors
```

Wire them into `main(target)` next to the existing checks. Locate the loop that accumulates errors per overlay path and add both calls:

```python
        errors.extend(check_threat_kind_id_coupling(overlay, path))
        errors.extend(check_no_empty_taxonomy_buckets(overlay, path))
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_lint.py -v
```

Expected: all lint tests pass (including the two new ones).

- [x] **Step 6: Commit**

```bash
git add tools/lint.py tests/test_lint.py
git commit -m "Apply threat_kind and empty-bucket checks to overlay linter"
```

---

## Task 5: Fixture — bad nano Flowise candidate (regression record)

**Files:**
- Create: `tests/fixtures/candidates/flowise-nano-bad.yaml`
- Test: `tests/test_seed_validator.py`

- [x] **Step 1: Create the fixture**

`tests/fixtures/candidates/flowise-nano-bad.yaml` — the literal nano-style output for GHSA-mq53-pc65-wjc4, slimmed to the openaca-relevant fields:

```yaml
schema_version: 1.7.5
id: GHSA-mq53-pc65-wjc4
modified: '2026-05-14T16:40:00.445224Z'
_candidate:
  review_status: needs_review
  matched_by:
    - package_name_agent_stack
  package_names:
    - flowise
  annotation_source: llm
  llm_provider: openai
  llm_model: gpt-5.4-nano
_evidence:
  - field: summary
    quote: 'FlowiseAI: Evaluation create+update mass-assignment allows cross-workspace evaluation takeover'
database_specific:
  openaca:
    evidence_level: likely
    taxonomies:
      mitre_atlas:
        - AML.T0010.001
      owasp_agentic_skills_top10: []
      owasp_agentic_top10:
        - asi04
      owasp_llm_top10:
        - llm03:2025
      owasp_mcp_top10: []
      supplemental_taxonomies: {}
    threat_kind: malicious_package
summary: 'FlowiseAI: Evaluation create+update mass-assignment allows cross-workspace evaluation takeover'
```

- [x] **Step 2: Write the failing test**

Append to `tests/test_seed_validator.py`:

```python
def test_fixture_flowise_nano_bad_is_rejected_with_actionable_errors():
    """The literal nano-style annotation must be rejected with errors that
    name each violation by field, so reviewers/agents can self-correct.
    """
    fixture = Path(__file__).resolve().parent / "fixtures" / "candidates" / "flowise-nano-bad.yaml"
    candidate = yaml.safe_load(fixture.read_text(encoding="utf-8"))

    errors = validate_candidate(candidate)

    joined = "\n".join(errors)
    # threat_kind on non-MAL record
    assert "threat_kind" in joined and "MAL-" in joined, joined
    # at least one empty-bucket error, naming a specific bucket
    assert "empty taxonomy bucket" in joined, joined
    assert "owasp_mcp_top10" in joined or "owasp_agentic_skills_top10" in joined, joined
```

Add imports if not already present:

```python
from pathlib import Path

import yaml
```

- [x] **Step 3: Run tests to verify failure mode is right**

```bash
uv run pytest tests/test_seed_validator.py::test_fixture_flowise_nano_bad_is_rejected_with_actionable_errors -v
```

Expected: passes (Tasks 2 and 3 already implemented the checks; this test just exercises them through a real-world fixture).

- [x] **Step 4: Commit**

```bash
git add tests/fixtures/candidates/flowise-nano-bad.yaml tests/test_seed_validator.py
git commit -m "Add nano-Flowise regression fixture asserting validator rejects each violation"
```

---

## Task 6: Fixture — good corrected Flowise candidate

**Files:**
- Create: `tests/fixtures/candidates/flowise-corrected-good.yaml`
- Test: `tests/test_seed_validator.py`

- [x] **Step 1: Create the fixture**

`tests/fixtures/candidates/flowise-corrected-good.yaml` — opus-style correct annotation:

```yaml
schema_version: 1.7.5
id: GHSA-mq53-pc65-wjc4
modified: '2026-05-14T16:40:00.445224Z'
_candidate:
  review_status: needs_review
  matched_by:
    - package_name_agent_stack
  package_names:
    - flowise
  annotation_source: llm
  llm_provider: anthropic
  llm_model: claude-opus-4-7
_evidence:
  - field: summary
    quote: 'FlowiseAI: Evaluation create+update mass-assignment allows cross-workspace evaluation takeover'
  - field: details
    quote: 'Evaluation runs (which may include captured prompts, model outputs, scoring data) can be moved cross-workspace via `workspaceId` overwrite.'
database_specific:
  openaca:
    taxonomies:
      owasp_agentic_top10:
        - asi03
      owasp_llm_top10:
        - llm02:2025
    evidence_level: likely
summary: 'FlowiseAI: Evaluation create+update mass-assignment allows cross-workspace evaluation takeover'
```

- [x] **Step 2: Write the passing test**

Append to `tests/test_seed_validator.py`:

```python
def test_fixture_flowise_corrected_good_validates():
    """Forward-compatible record of 'this is what a correct annotation looks like'."""
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "candidates"
        / "flowise-corrected-good.yaml"
    )
    candidate = yaml.safe_load(fixture.read_text(encoding="utf-8"))

    assert validate_candidate(candidate) == []
```

- [x] **Step 3: Run test**

```bash
uv run pytest tests/test_seed_validator.py::test_fixture_flowise_corrected_good_validates -v
```

Expected: passes.

- [x] **Step 4: Commit**

```bash
git add tests/fixtures/candidates/flowise-corrected-good.yaml tests/test_seed_validator.py
git commit -m "Add corrected Flowise fixture as the canonical good-annotation reference"
```

---

## Task 7: Skill template for Claude Code

**Files:**
- Create: `examples/skills/claude/openaca-candidate-review/SKILL.md`

- [x] **Step 1: Create the skill template**

```markdown
---
name: openaca-candidate-review
description: Annotate or re-review OpenACA seed candidates in `candidates/` according to the canonical rules. Use when the user asks to annotate, review, fix, or re-classify candidate YAML files produced by the deterministic seeder.
---

# OpenACA candidate review

Annotate or re-review reviewable seed candidates produced by
`openaca seed`. Uses your Claude Code session's auth (subscription
quota), not the API LLM provider path.

## Invocation modes

- `/openaca-candidate-review candidates/GHSA-mq53-pc65-wjc4.yaml` — single file
- `/openaca-candidate-review candidates/` — every candidate whose
  `_candidate.review_status` is `needs_review`

For directory mode, if there are more than 20 candidates to review,
stop and ask the user to split into smaller batches; quality
degrades past ~20 records per session due to context budget.

## What to do

1. **Locate the OpenACA repo.** It is the working directory if the
   user invoked from inside it; otherwise ask once.
2. **Read the rules.** Read `docs/seed-review-rules.md` in full.
   This is the canonical contract. If the file is missing, stop and
   report — the repo is not in a state to be reviewed.
3. **Read the framework references.** Read all of
   `docs/frameworks/*.md` (typically OWASP Agentic Top 10, OWASP LLM
   Top 10, OWASP MCP Top 10, MITRE ATLAS, OWASP Agentic Skills Top
   10). Hold them as context for taxonomy mapping decisions.
4. **For each candidate file:**
   - Read the file.
   - Determine review mode: `annotate` (no
     `database_specific.openaca`) or `re-review` (block exists).
   - Read the OSV record fields in the file (`summary`, `details`,
     `affected`, `references`).
   - Apply the rules from `docs/seed-review-rules.md`:
     - Edit ONLY `database_specific.openaca.taxonomies.*` and
       `database_specific.openaca.evidence_level`.
     - NEVER touch `threat_kind`, `_candidate`, `_evidence`,
       `summary`, `details`, `affected`, `severity`, `references`,
       `aliases`, `id`, `modified`.
     - Omit taxonomy keys that do not apply. Do not emit `[]` or
       `{}`.
     - Do not classify ordinary bugs in agent-stack packages as
       supply-chain (`asi04`, `llm03:2025`, ATLAS supply-chain
       techniques) unless the OSV record describes malicious,
       compromised, typosquat, or dependency-confusion behavior.
     - For each taxonomy assertion, hold a defensible quote from
       the OSV `summary`/`details` in mind. Do not assert mappings
       you cannot defend.
   - Write the edited YAML back to the same path using the Edit
     tool.
5. **Validate.** After all edits, run:
   ```
   uv run openaca lint candidates/
   ```
   If validation fails, read the error message, correct the
   relevant candidate, and re-run validation. Do not move on with
   unresolved validation errors.
6. **Summarize.** Report:
   - Number of candidates reviewed / annotated / re-reviewed.
   - Any candidates left as-is (and why).
   - Any validation failures (and the corrective edit applied).

## Out of scope

- Do not edit overlays under `overlays/` from this skill. Use
  `openaca promote` for promotion after candidate review is complete.
- Do not change OSV-owned fields. If the OSV record itself looks
  wrong, surface it to the user rather than editing the candidate.
- Do not invent taxonomies that are not in
  `docs/frameworks/*.md`.

## When the rules and your judgment disagree

The rules win. If a rule prohibits something you believe should be
allowed, finish the current review with the existing rules and
then surface the disagreement to the user in your summary message.
Do not silently apply your own judgment over the documented rule.
```

- [x] **Step 2: Commit**

```bash
git add examples/skills/claude/openaca-candidate-review/SKILL.md
git commit -m "Add Claude Code skill template for candidate review"
```

---

## Task 8: README pointer

**Files:**
- Modify: `README.md`

- [x] **Step 1: Locate the right section**

```bash
cd /Users/vinodkone/workspace/openaca/.worktrees/candidate-annotation-surface
grep -n "openaca seed\|seeding\|candidate" README.md | head -10
```

Identify where the existing seed/candidate flow is documented. Likely under a "Seeding" or "Workflows" section.

- [x] **Step 2: Add the local-subscription flow snippet**

Append a subsection (or insert near the existing `openaca seed` documentation, matching the file's existing structure and headline level):

```markdown
### Local annotation via Claude Code (subscription quota)

For human-in-the-loop triage without burning API credits:

1. Run the deterministic seeder to populate `candidates/`:
   ```
   uv run openaca seed candidates/ --no-llm
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
   `openaca lint` on the result. See `docs/seed-review-rules.md` for
   the exact editable surface.

API-mode annotation (`--llm-provider openai|anthropic`) remains
available for CI and batch runs.
```

- [x] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document local subscription-based annotation flow in README"
```

---

## Task 9: Full gate

- [x] **Step 1: Run the full test suite and lint gates**

```bash
cd /Users/vinodkone/workspace/openaca/.worktrees/candidate-annotation-surface
uv run ruff format --check tools/ tests/
uv run ruff check tools/ tests/
uv run pyright
uv run pytest -q
uv run openaca lint overlays/
```

Expected: all green. The overlay linter run validates that no existing canonical overlays trip the new rules — important since this PR adds enforcement that wasn't there before.

- [x] **Step 2: If `openaca lint overlays/` reports new failures**

These would be existing canonical overlays that violate the new rules. Two possibilities:

1. A canonical overlay has `threat_kind: malicious_package` but is not a MAL- record → the overlay is wrong and needs correcting at the source. Stop and report to the user.
2. A canonical overlay has an empty taxonomy bucket → same: fix the overlay (omit the key).

Do not weaken the rules to accommodate broken existing data. Report the offending overlays in the summary and let the user decide whether to fix the overlays in this PR or in a follow-up.

- [x] **Step 3: Push the branch and open the PR**

```bash
git push -u origin feat/candidate-annotation-surface
gh pr create --title "Constrain candidate annotation surface; add review skill template" \
  --body "$(cat <<'EOF'
## Summary

- Lock the LLM/agent-editable surface of OpenACA candidates to `database_specific.openaca.taxonomies.*` and `database_specific.openaca.evidence_level`. Everything else is either seeder-owned (`threat_kind` from MAL-* coupling) or upstream-owned (severity, affected, references, summary, details).
- Enforce the contract structurally: validator and overlay linter reject `threat_kind` on non-MAL records and reject empty taxonomy buckets. Actionable error messages name the field.
- Add a Claude Code skill template for local subscription-based annotation. Repo owns the rules (`docs/seed-review-rules.md`); the skill is a thin instruction wrapper that points at them. Codex CLI parity is a follow-up if needed.
- Regression fixtures: nano-style Flowise output (bad) and the corrected opus-style annotation (good).

## Validation

- uv run pytest -q
- uv run ruff format --check tools/ tests/
- uv run ruff check tools/ tests/
- uv run pyright
- uv run openaca lint overlays/

## Out of scope (tracked separately)

- Prompt fix in `tools/seed/llm.py` for `INSTRUCTIONS` (threat_kind underspecification). Lower priority once API mode is opt-in for CI/automation only.
- `claude-cli` / `codex-cli` providers in `tools/seed/llm.py`. Deferred until the skill flow proves insufficient for real triage.
EOF
)"
```

- [x] **Step 4: Verify CI passes**

```bash
gh pr checks --watch
```

Expected: green.

---

## Critical files referenced

- `tools/seed/validator.py` — candidate validator (existing, extended)
- `tools/lint.py` — overlay linter (existing, extended)
- `tools/promote.py:31` — `project_candidate_to_overlay` (read-only; validator uses it)
- `schema/openaca.schema.json:96` — `threat_kind` enum and `additionalProperties: false` on `openaca_extension`
- `docs/adrs/0012-minimal-overlay-schema.md` — defines `threat_kind` semantics
- `docs/frameworks/*.md` — referenced by the skill at runtime
- `examples/skills/claude/openaca-candidate-review/SKILL.md` — new

## Reused utilities

- `jsonschema.Draft202012Validator` — already in `validator.py` and `lint.py`
- `tools.promote.project_candidate_to_overlay` — for stripping non-canonical keys before schema validation
- Existing `_candidate()` test fixture in `test_seed_validator.py` — reused for all new tests

## What's out of scope

- CLI providers (`claude-cli`, `codex-cli`) in the seeder. Deferred.
- Prompt-side fix for API-mode `threat_kind` underspecification. Tracking issue.
- Codex CLI skill template. Single-CLI ship first.
- Annotation-source provenance changes. The `_candidate.annotation_source` field stays as-is.
