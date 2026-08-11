# Plan 039 — Default taxonomy surfacing in scan output

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

> Implements `docs/specs/default-taxonomy-surfacing.md` and ADR-0043 (written in
> Task 1). Overlay taxonomies are merged into every OSV record at scan time but
> render only under `-v` and on the static site. This plan surfaces
> `owasp_agentic_top10` in the default text card and carries the full taxonomy
> block through the JSON envelope and SARIF properties.

**Goal:** A default (non-`-v`) scan prints `owasp-asi: ASINN  [owasp-agentic-top-10-2026]` under each finding
whose advisory carries an OpenACA overlay, and the JSON and SARIF outputs carry
the complete `taxonomies` block.

**Architecture:** One pure extractor, `overlay_taxonomies(advisory)`, added to
`tools/finding_output.py` beside the existing normalizers. Three consumers call
it: the text renderer (`tools/render.py`), the JSON envelope builder
(`finding_to_output`), and SARIF — which is free, because `_properties_for`
already calls `finding_to_output`. The `isinstance`-guarded
`database_specific → openaca → taxonomies` walk currently inlined in the verbose
renderer stops being duplicated.

**Tech stack:** Python / uv. Gate: `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pyright`, `uv run pytest`.

## Global Constraints

- Machine formats (JSON, SARIF) carry **all** taxonomy families. The
  single-family display subset is a text-card concern only.
- The default text card shows **only** `owasp_agentic_top10`.
- When a family is absent, empty, or the advisory has no overlay, emit
  **nothing** — no placeholder, no empty key.
- The JSON/SARIF key is `taxonomies`, never `standards`. `standards` is the
  posture-finding key and stays distinct.
- Under `-v`, the generic `taxonomies:` line must **not** repeat
  `owasp_agentic_top10` — the default line already showed it.
- Default to writing no comments; add one only where the *why* is non-obvious.
- Every task ends green on `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `docs/adrs/0043-default-taxonomy-surfacing.md` | Records the display-subset decision; amends ADR-0016's envelope | 1 |
| `docs/adrs/INDEX.md` | Index entry for ADR-0043 | 1 |
| `tools/finding_output.py` | `overlay_taxonomies()` extractor + `taxonomies` in the JSON envelope | 2, 4 |
| `tests/test_finding_output.py` | Extractor unit tests + envelope test | 2, 4 |
| `tests/test_scan.py` | Scan-level JSON output carries the key | 4 |
| `tools/render.py` | Default `owasp-asi:` line; verbose line stops repeating the agentic family | 3 |
| `tests/test_render.py` | Text-card behavior | 3 |
| `tools/sarif.py` | `properties.taxonomies` | 5 |
| `tests/test_sarif.py` | SARIF property test | 5 |
| `tests/test_e2e.py` | Cross-layer: real corpus → default text card | 6 |

---

## Task 1: ADR-0043

Doc-only. No test cycle — ADRs are prose. This lands before any code, per the
CLAUDE.md ADR gate.

**Files:**
- Create: `docs/adrs/0043-default-taxonomy-surfacing.md`
- Modify: `docs/adrs/INDEX.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the decision every later task implements. No code symbols.

- [ ] **Step 1: Write the ADR.**

Create `docs/adrs/0043-default-taxonomy-surfacing.md`, following
`docs/adrs/TEMPLATE.md` exactly:

```markdown
---
id: 0043
title: Surface the agentic taxonomy by default; carry all list-valued families in machine formats
status: accepted
date: 2026-08-10
supersedes: null
superseded-by: null
amends: 0016
---

## Context

The scanner merges each bundled overlay into the OSV record it returns, so
`database_specific.openaca.taxonomies` is in memory for the whole run. Only two
surfaces render it: `-v` text output and the static HTML site. The default text
card, the JSON envelope, and SARIF all drop it.

Posture findings already behave the other way. `tools/render.py` prints
`standards:` in the default card with no verbose gate, and both the JSON
envelope and SARIF properties carry `standards`. A posture rule reports its ASI
code by default while a matched advisory carrying the same ASI code hides it
behind `-v`.

ADR-0035 states that "`source_version`, `confidence`, `evidence`, and taxonomy
fields travel with the finding where available." That clause sits in the
paragraph defining `source`, and concerns attribution-adjacent metadata
crossing the external-scanner adapter boundary — not which output surfaces
render advisory overlay data. Surfacing `taxonomies` in JSON and SARIF for
vulnerability findings is consistent with that same principle, extended here
to a surface ADR-0035 did not itself decide; the field is read from the
advisory record at output time, not stamped onto the `Finding` dataclass (see
Alternatives).

What forced the decision now is the display question, which ADR-0035 does not
answer: across the 107 bundled overlays, all 107 carry `owasp_agentic_top10`,
101 carry `owasp_llm_top10`, 101 carry `mitre_atlas`, and 71 carry
`owasp_mcp_top10`. Rendering every family in the default card costs up to four
lines per finding.

## Decision

The default text card renders `owasp_agentic_top10` only, as a single
`owasp-asi: <CODES>  [owasp-agentic-top-10-2026]` line under each finding whose
advisory carries one. Codes are uppercased to match OWASP's own presentation,
and the bracketed edition marker mirrors the `[osv.dev]` source marker on the
finding line above, so a reader can tell which framework and which edition the
codes belong to without leaving the card.

JSON and SARIF carry the complete `taxonomies` block: all list-valued families;
`supplemental_taxonomies` is excluded because it is an object of arrays, not a
list, pending a shape decision. When a family is absent or the advisory has no
overlay, nothing is emitted — no placeholder key, no "unmapped" marker.

The machine-format field is named `taxonomies`, matching the overlay schema key
and ADR-0035's own phrasing. It stays distinct from posture's `standards`.

This amends the JSON envelope that ADR-0016's Consequences enumerate, adding a
top-level `taxonomies` key for vulnerability findings. It does not supersede
ADR-0016; the identity-layer separation that ADR stands on is unchanged.

## Alternatives considered

- **Render all families in the default card**: rejected because ten findings
  would add roughly forty lines, burying the severity and fix lines the card
  exists to surface. `-v` still shows every family, so nothing is lost.
- **Resolve each code to its OWASP category name inline** (`ASI02 Tool Misuse
  and Exploitation`): rejected for the default card. It answers "what is ASI02"
  without a lookup, but two codes with long names push the line past 95
  characters and wrap in a standard terminal, and it requires a code-to-name map
  in code that duplicates `docs/frameworks/owasp-agentic-ai-top-10-2026.md` and
  drifts from it silently. The bracketed edition marker points a reader at the
  framework instead, at a fraction of the width and with nothing to keep in
  sync. Revisit if the card ever gains a legend section, where names would cost
  one block per scan rather than one line per finding.
- **Bare lowercase codes with no label** (`agentic: asi02, asi05`): rejected
  because the codes read as an internal slug rather than a public identifier,
  and nothing on the line says OWASP. The finding line above it ends in
  `[osv.dev]`; a taxonomy line with no comparable attribution is the weaker
  half of the same card.
- **Render agentic + MCP in the default card**: rejected because
  `owasp_mcp_top10` covers only 71 of 107 overlays, so the line would be
  intermittent, and "why these two" is a judgment call with no principle behind
  it that a later reader could reconstruct.
- **Print an explicit `unmapped` marker when no overlay exists**: rejected
  because most findings have no overlay — the corpus is 107 records while
  OSV.dev returns advisories for anything matched — so the marker would be the
  common case and would read as a defect rather than as normal.
- **Name the machine-format field `standards` to match posture findings**:
  rejected because posture standards are rule-authored and overlay taxonomies
  are corpus-authored. ADR-0035 keeps claim type and source orthogonal;
  collapsing the field names would hide a provenance difference consumers may
  want to filter on.
- **Stamp taxonomies onto the `Finding` dataclass at match time**: rejected
  because it copies overlay data onto the finding when it already lives in
  `advisory_index`, creating two sources of truth. ADR-0012 keeps overlay
  content in the advisory record.
- **Widen the posture `Standards` dataclass and reuse it for advisories**:
  rejected because `Standards` carries `cwe`, `openssf_scorecard`, and `slsa`
  that overlays never populate, and lacks `mitre_atlas` and `owasp_llm_top10`
  that overlays do. Widening a frozen dataclass constructed by five posture
  rules is a larger blast radius than the problem justifies.

## Consequences

A default scan now shows the agent-context mapping that motivates the overlay
corpus, instead of requiring `-v` to discover it exists. Downstream consumers of
the JSON envelope and SARIF gain the full taxonomy block without re-reading
`overlays/`.

`-v` becomes strictly additive rather than a superset that duplicates its own
default: the generic `taxonomies:` line stops repeating `owasp_agentic_top10`.
Anyone parsing `-v` text output for that family — an unsupported use, but
possible — breaks.

The default card grows one line per overlaid finding. On a scan where no
matched advisory has an overlay, the feature is invisible, which is the accepted
cost of silent omission.

The display subset is now a maintained decision: the schema already defines
five list-valued taxonomy families plus `supplemental_taxonomies`, and adding
or populating another one does not automatically put it in the card — only
`owasp_agentic_top10` renders there.

## When to revisit

Revisit if `owasp_mcp_top10` corpus coverage approaches that of
`owasp_agentic_top10`, which would weaken the intermittency argument against a
second default line. Revisit if a consumer needs corpus-coverage visibility on
scans where nothing is overlaid — that is the Summary-rollup option this
decision declined. Revisit the field name if Fleet or another consumer needs to
treat posture `standards` and overlay `taxonomies` as one queryable field.
```

- [ ] **Step 2: Add the INDEX entry.**

In `docs/adrs/INDEX.md`, append to the end of the `## Active` list, matching the
existing one-liner style:

```markdown
- [ADR-0043 — Surface the agentic taxonomy by default; carry all list-valued families in machine formats](0043-default-taxonomy-surfacing.md): the default text card renders `owasp_agentic_top10` only (one `owasp-asi: <CODES>  [owasp-agentic-top-10-2026]` line per finding); JSON and SARIF carry the full `taxonomies` block under a `taxonomies` key distinct from posture's `standards`; absent families emit nothing. Extends ADR-0035's taxonomy-travel principle to vulnerability findings' JSON/SARIF output and **amends ADR-0016's** enumerated JSON envelope.
```

- [ ] **Step 3: Commit.**

```bash
git add docs/adrs/0043-default-taxonomy-surfacing.md docs/adrs/INDEX.md
git commit -m "docs(adr): ADR-0043 default taxonomy surfacing in scan output"
```

---

## Task 2: The `overlay_taxonomies` extractor

**Files:**
- Modify: `tools/finding_output.py`
- Test: `tests/test_finding_output.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `overlay_taxonomies(advisory: dict | None) -> dict[str, list[str]]`,
  importable as `from tools.finding_output import overlay_taxonomies`. Tasks 3,
  4, and 5 all call it. Returns a dict mapping family name to a non-empty list
  of strings; returns `{}` when there is nothing to report.

- [ ] **Step 1: Write the failing tests.**

Append to `tests/test_finding_output.py`:

```python
from tools.finding_output import overlay_taxonomies


def test_overlay_taxonomies_extracts_all_families():
    advisory = {
        "id": "GHSA-X",
        "database_specific": {
            "openaca": {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi02", "asi05"],
                    "mitre_atlas": ["AML.T0051"],
                },
                "evidence_level": "confirmed",
            }
        },
    }
    assert overlay_taxonomies(advisory) == {
        "owasp_agentic_top10": ["asi02", "asi05"],
        "mitre_atlas": ["AML.T0051"],
    }


def test_overlay_taxonomies_returns_empty_without_overlay():
    assert overlay_taxonomies(None) == {}
    assert overlay_taxonomies({}) == {}
    assert overlay_taxonomies({"id": "GHSA-X"}) == {}
    assert overlay_taxonomies({"database_specific": {}}) == {}
    assert overlay_taxonomies({"database_specific": {"openaca": {}}}) == {}


def test_overlay_taxonomies_tolerates_malformed_blocks():
    # Upstream OSV records are third-party data; a non-dict openaca block or a
    # non-list family value must not raise.
    assert overlay_taxonomies({"database_specific": "nope"}) == {}
    assert overlay_taxonomies({"database_specific": {"openaca": "nope"}}) == {}
    assert overlay_taxonomies(
        {"database_specific": {"openaca": {"taxonomies": "nope"}}}
    ) == {}
    assert overlay_taxonomies(
        {"database_specific": {"openaca": {"taxonomies": {"owasp_agentic_top10": "asi02"}}}}
    ) == {}


def test_overlay_taxonomies_drops_empty_families_and_coerces_members():
    advisory = {
        "database_specific": {
            "openaca": {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi02"],
                    "owasp_mcp_top10": [],
                    "mitre_atlas": [1],
                }
            }
        }
    }
    assert overlay_taxonomies(advisory) == {
        "owasp_agentic_top10": ["asi02"],
        "mitre_atlas": ["1"],
    }
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `uv run pytest tests/test_finding_output.py -k overlay_taxonomies -v`

Expected: FAIL — `ImportError: cannot import name 'overlay_taxonomies' from 'tools.finding_output'`

- [ ] **Step 3: Implement the extractor.**

In `tools/finding_output.py`, add after `_matched_advisory_for` (around line 127)
and before `finding_to_output`:

```python
def overlay_taxonomies(advisory: dict | None) -> dict[str, list[str]]:
    """Extract `database_specific.openaca.taxonomies` from an advisory record.

    Upstream OSV records are third-party data, so every level is guarded.
    Families with a missing, non-list, or empty value are dropped.
    """
    if not isinstance(advisory, dict):
        return {}
    database_specific = advisory.get("database_specific")
    if not isinstance(database_specific, dict):
        return {}
    openaca = database_specific.get("openaca")
    if not isinstance(openaca, dict):
        return {}
    taxonomies = openaca.get("taxonomies")
    if not isinstance(taxonomies, dict):
        return {}
    out: dict[str, list[str]] = {}
    for family, values in taxonomies.items():
        if isinstance(values, list) and values:
            out[str(family)] = [str(v) for v in values]
    return out
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `uv run pytest tests/test_finding_output.py -k overlay_taxonomies -v`

Expected: PASS, 4 tests.

- [ ] **Step 5: Run the full gate.**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`

Expected: all green. Nothing calls the new function yet, so no existing test changes behavior.

- [ ] **Step 6: Commit.**

```bash
git add tools/finding_output.py tests/test_finding_output.py
git commit -m "feat(output): add overlay_taxonomies extractor"
```

---

## Task 3: Default `owasp-asi:` line in the text card

Both the card path and the legacy path route through `_render_finding_groups`,
so one edit covers both.

**Files:**
- Modify: `tools/render.py:424-451` (the per-finding loop inside `_render_finding_groups`)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `overlay_taxonomies` from Task 2.
- Produces: no new symbols. Text-output contract only: a line
  `        owasp-asi: <CODES>  [<edition>]` at eight-space indent, directly
  under the finding's severity line.

- [ ] **Step 1: Write the failing tests.**

Add to `tests/test_render.py`, after the existing
`test_text_verbose_adds_taxonomies_and_evidence_level`:

```python
def test_text_default_shows_agentic_taxonomy():
    findings = [_finding("X", "pkg", "1.0.0")]
    advisory = _advisory("X", "npm", "pkg", severity_label="HIGH")
    advisory["database_specific"]["openaca"]["taxonomies"] = {
        "owasp_agentic_top10": ["asi02", "asi05"],
        "mitre_atlas": ["AML.T0051"],
    }
    index = {"X": advisory}
    out = render_text(findings, index, _stats(), verbose=False)
    assert "owasp-asi: ASI02, ASI05  [owasp-agentic-top-10-2026]" in out
    # Other families stay behind -v.
    assert "AML.T0051" not in out


def test_text_default_omits_agentic_line_without_overlay():
    findings = [_finding("X", "pkg", "1.0.0")]
    index = {"X": _advisory("X", "npm", "pkg", severity_label="HIGH")}
    out = render_text(findings, index, _stats(), verbose=False)
    assert "owasp-asi:" not in out


def test_text_default_omits_agentic_line_when_family_absent():
    findings = [_finding("X", "pkg", "1.0.0")]
    advisory = _advisory("X", "npm", "pkg", severity_label="HIGH")
    advisory["database_specific"]["openaca"]["taxonomies"] = {
        "mitre_atlas": ["AML.T0051"],
    }
    index = {"X": advisory}
    out = render_text(findings, index, _stats(), verbose=False)
    assert "owasp-asi:" not in out


def test_text_verbose_does_not_repeat_agentic_family():
    """-v is additive: the generic taxonomies line covers the families the
    default `owasp-asi:` line did not already show."""
    findings = [_finding("X", "pkg", "1.0.0")]
    advisory = _advisory("X", "npm", "pkg", severity_label="HIGH")
    advisory["database_specific"]["openaca"]["taxonomies"] = {
        "owasp_agentic_top10": ["asi02"],
        "mitre_atlas": ["AML.T0051"],
    }
    index = {"X": advisory}
    out = render_text(findings, index, _stats(), verbose=True)
    assert "owasp-asi: ASI02  [owasp-agentic-top-10-2026]" in out
    assert "taxonomies: mitre_atlas=AML.T0051" in out
    assert "owasp_agentic_top10=" not in out


def test_text_verbose_omits_taxonomies_line_when_only_agentic_present():
    findings = [_finding("X", "pkg", "1.0.0")]
    advisory = _advisory("X", "npm", "pkg", severity_label="HIGH")
    advisory["database_specific"]["openaca"]["taxonomies"] = {
        "owasp_agentic_top10": ["asi02"],
    }
    index = {"X": advisory}
    out = render_text(findings, index, _stats(), verbose=True)
    assert "owasp-asi: ASI02  [owasp-agentic-top-10-2026]" in out
    assert "taxonomies:" not in out
```

- [ ] **Step 2: Update the existing verbose test, which now asserts stale behavior.**

`tests/test_render.py:403` currently reads:

```python
def test_text_verbose_adds_taxonomies_and_evidence_level():
    findings = [_finding("X", "pkg", "1.0.0")]
    advisory = _advisory("X", "npm", "pkg", severity_label="HIGH")
    advisory["database_specific"]["openaca"]["taxonomies"] = {
        "owasp_agentic_top10": ["asi02", "asi05"]
    }
    advisory["database_specific"]["openaca"]["evidence_level"] = "confirmed"
    index = {"X": advisory}
    out_v = render_text(findings, index, _stats(), verbose=True)
    out_p = render_text(findings, index, _stats(), verbose=False)
    assert "taxonomies: owasp_agentic_top10=asi02,asi05" in out_v
    assert "evidence_level: confirmed" in out_v
    assert "confidence:" in out_v
    assert "taxonomies:" not in out_p
```

Replace it with — note the overlay now carries a second family so the generic
line still has something to print, and the name changes to describe what it
actually covers:

```python
def test_text_verbose_adds_evidence_level_and_remaining_taxonomies():
    findings = [_finding("X", "pkg", "1.0.0")]
    advisory = _advisory("X", "npm", "pkg", severity_label="HIGH")
    advisory["database_specific"]["openaca"]["taxonomies"] = {
        "owasp_agentic_top10": ["asi02", "asi05"],
        "owasp_mcp_top10": ["mcp03:2025"],
    }
    advisory["database_specific"]["openaca"]["evidence_level"] = "confirmed"
    index = {"X": advisory}
    out_v = render_text(findings, index, _stats(), verbose=True)
    out_p = render_text(findings, index, _stats(), verbose=False)
    assert "taxonomies: owasp_mcp_top10=mcp03:2025" in out_v
    assert "evidence_level: confirmed" in out_v
    assert "confidence:" in out_v
    # The agentic family is shown by default, so -v must not repeat it.
    assert "owasp_agentic_top10=" not in out_v
    # The generic taxonomies line stays verbose-only.
    assert "taxonomies:" not in out_p
    assert "owasp-asi: ASI02, ASI05  [owasp-agentic-top-10-2026]" in out_p
```

- [ ] **Step 3: Run the tests to verify they fail.**

Run: `uv run pytest tests/test_render.py -k "agentic or taxonom" -v`

Expected: FAIL. The new tests fail on the missing `owasp-asi:` line; the rewritten
test fails on `owasp_agentic_top10=` still appearing under `-v`.

- [ ] **Step 4: Implement.**

In `tools/render.py`, extend the existing `finding_output` import at line 34 —
do not add a second import line:

```python
from tools.finding_output import (
    finding_to_output,
    observation_to_output,
    overlay_taxonomies,
    posture_to_output,
)
```

Then replace the per-finding block at `tools/render.py:424-450`. Current code:

```python
        for f in findings_sorted:
            adv = advisory_index.get(f.advisory_id) or {}
            label = derive_severity_label(adv)
            label_disp = _color(label, use_color)
            fixed_in = _fixed_in_for_finding(f, adv) or "no fix"
            summary = _summary_for_advisory(adv)
            source = _source_for_advisory(adv) or "openaca.dev"
            out.append(
                f"  {label_disp}  {f.advisory_id}  fixed in {fixed_in}  {summary}  [{source}]"
            )
            if verbose:
                ds_openaca = (adv.get("database_specific") or {}).get("openaca") or {}
                if not isinstance(ds_openaca, dict):
                    ds_openaca = {}
                taxonomies = ds_openaca.get("taxonomies") or {}
                if isinstance(taxonomies, dict):
                    taxonomy_parts = []
                    for family, values in sorted(taxonomies.items()):
                        if isinstance(values, list) and values:
                            taxonomy_parts.append(f"{family}={','.join(str(v) for v in values)}")
                    if taxonomy_parts:
                        out.append(f"        taxonomies: {'; '.join(taxonomy_parts)}")
                evidence_level = ds_openaca.get("evidence_level")
                if isinstance(evidence_level, str):
                    out.append(f"        evidence_level: {evidence_level}")
                out.append(f"        confidence: {f.confidence}")
                out.extend(f"        {line}" for line in _identity_detail_lines(f))
```

Replacement:

```python
        for f in findings_sorted:
            adv = advisory_index.get(f.advisory_id) or {}
            label = derive_severity_label(adv)
            label_disp = _color(label, use_color)
            fixed_in = _fixed_in_for_finding(f, adv) or "no fix"
            summary = _summary_for_advisory(adv)
            source = _source_for_advisory(adv) or "openaca.dev"
            out.append(
                f"  {label_disp}  {f.advisory_id}  fixed in {fixed_in}  {summary}  [{source}]"
            )
            taxonomies = overlay_taxonomies(adv)
            agentic = taxonomies.get("owasp_agentic_top10")
            if agentic:
                codes = ", ".join(code.upper() for code in agentic)
                out.append(f"        owasp-asi: {codes}  [{_ASI_FRAMEWORK}]")
            if verbose:
                ds_openaca = (adv.get("database_specific") or {}).get("openaca") or {}
                if not isinstance(ds_openaca, dict):
                    ds_openaca = {}
                # The agentic family already printed above; -v adds the rest.
                taxonomy_parts = [
                    f"{family}={','.join(values)}"
                    for family, values in sorted(taxonomies.items())
                    if family != "owasp_agentic_top10"
                ]
                if taxonomy_parts:
                    out.append(f"        taxonomies: {'; '.join(taxonomy_parts)}")
                evidence_level = ds_openaca.get("evidence_level")
                if isinstance(evidence_level, str):
                    out.append(f"        evidence_level: {evidence_level}")
                out.append(f"        confidence: {f.confidence}")
                out.extend(f"        {line}" for line in _identity_detail_lines(f))
```

`ds_openaca` is still needed for `evidence_level`, so it stays.

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `uv run pytest tests/test_render.py -v`

Expected: PASS, whole file. If another test in the file asserts on default-card
line counts or exact card text, it may need the new line accounted for — fix by
updating the expectation, not by gating the new line.

- [ ] **Step 6: Run the full gate.**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`

Expected: all green. `tests/test_scan.py` and `tests/test_e2e.py` assert on scan
text output; if any assert exact card contents, update those expectations here.

- [ ] **Step 7: Commit.**

```bash
git add tools/render.py tests/test_render.py
git commit -m "feat(render): show owasp_agentic_top10 in the default text card"
```

---

## Task 4: `taxonomies` in the JSON envelope

**Files:**
- Modify: `tools/finding_output.py:130-163` (`finding_to_output`)
- Test: `tests/test_finding_output.py`

**Interfaces:**
- Consumes: `overlay_taxonomies` from Task 2.
- Produces: `finding_to_output(...)` gains an optional `"taxonomies"` key mapping
  family name to `list[str]`, present only when non-empty. Task 5 depends on
  this, because `_properties_for` reads the output of `finding_to_output`.

- [ ] **Step 1: Write the failing tests.**

Append to `tests/test_finding_output.py`:

```python
def test_finding_to_output_carries_all_taxonomy_families():
    ref = ComponentRef(
        ecosystem="npm",
        name="pkg",
        version="1.0.0",
        source_manifest="package.json",
    )
    advisory = {
        "id": "GHSA-X",
        "summary": "test",
        "database_specific": {
            "openaca": {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi02"],
                    "mitre_atlas": ["AML.T0051"],
                }
            }
        },
    }
    out = finding_to_output(Finding("GHSA-X", ref, "high"), advisory)
    assert out["taxonomies"] == {
        "owasp_agentic_top10": ["asi02"],
        "mitre_atlas": ["AML.T0051"],
    }


def test_finding_to_output_omits_taxonomies_key_without_overlay():
    ref = ComponentRef(
        ecosystem="npm",
        name="pkg",
        version="1.0.0",
        source_manifest="package.json",
    )
    out = finding_to_output(Finding("GHSA-X", ref, "high"), {"id": "GHSA-X"})
    assert "taxonomies" not in out
```

If `ComponentRef` and `Finding` are not already imported in that test file, add:

```python
from tools.component_ref import ComponentRef
from tools.matcher import Finding
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `uv run pytest tests/test_finding_output.py -k taxonom -v`

Expected: FAIL — `KeyError: 'taxonomies'` on the first test.

- [ ] **Step 3: Implement.**

In `tools/finding_output.py`, inside `finding_to_output`, after the
`matched_advisory` entry is built and before the `attributed_to` block — that is,
directly after the `out: dict[str, Any] = {...}` literal closes:

```python
    taxonomies = overlay_taxonomies(advisory)
    if taxonomies:
        out["taxonomies"] = taxonomies
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `uv run pytest tests/test_finding_output.py -v`

Expected: PASS, whole file.

- [ ] **Step 5: Write the scan-level JSON test.**

The unit test above proves `finding_to_output` builds the key; it does not prove
`render_json` reaches the user with it. `render_json` calls `finding_to_output`,
so no code change is needed — but that wiring needs a test.

The `exposed-mcp` fixture depends on `@cyanheads/git-mcp-server@1.1.0`, which
matches `GHSA-3q26-f695-pp76`, whose corpus overlay carries
`owasp_agentic_top10: [asi02, asi05]`.

Append to `tests/test_scan.py`, after
`test_scan_format_json_produces_parseable_document`:

```python
def test_scan_format_json_carries_overlay_taxonomies():
    """The JSON envelope surfaces overlay taxonomies for an overlaid finding."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "repo",
            "--target",
            str(FIXTURES / "repos" / "exposed-mcp"),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 1, result.output
    output = result.output
    start = output.index("{")
    parsed = None
    for end in range(len(output), start, -1):
        try:
            parsed = json.loads(output[start:end])
            break
        except json.JSONDecodeError:
            continue
    assert parsed is not None

    overlaid = [
        f for f in parsed["findings"] if f.get("id") == "GHSA-3q26-f695-pp76"
    ]
    assert overlaid, parsed["findings"]
    assert overlaid[0]["taxonomies"]["owasp_agentic_top10"] == ["asi02", "asi05"]
```

- [ ] **Step 6: Run the scan-level test.**

Run: `uv run pytest tests/test_scan.py -k overlay_taxonomies -v`

Expected: PASS. If it fails on `overlaid` being empty, the fixture no longer
matches that advisory — check `uv run openaca scan repo --target tests/fixtures/repos/exposed-mcp --format json`
and retarget the assertion at whichever advisory ID the scan actually returns
with a `taxonomies` key.

- [ ] **Step 7: Run the full gate.**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`

Expected: all green. `tests/test_render.py` JSON tests and
`tests/test_scan.py:1594` assert with `<=` subset checks and should be
unaffected. If any test asserts an exact key set, add `taxonomies` to it.

- [ ] **Step 8: Commit.**

```bash
git add tools/finding_output.py tests/test_finding_output.py tests/test_scan.py
git commit -m "feat(output): carry overlay taxonomies in the JSON envelope"
```

---

## Task 5: `properties.taxonomies` in SARIF

**Files:**
- Modify: `tools/sarif.py:59-80` (`_properties_for`)
- Test: `tests/test_sarif.py`

**Interfaces:**
- Consumes: the `taxonomies` key on `finding_to_output` output from Task 4.
- Produces: no new symbols. SARIF contract only: `result.properties.taxonomies`,
  same dict shape as the JSON envelope.

- [ ] **Step 1: Write the failing tests.**

Append to `tests/test_sarif.py`:

```python
def test_sarif_result_carries_overlay_taxonomies():
    ref = ComponentRef(
        ecosystem="npm",
        name="pkg",
        version="1.0.0",
        source_manifest="package.json",
    )
    advisory = {
        "id": "GHSA-X",
        "database_specific": {
            "openaca": {
                "taxonomies": {
                    "owasp_agentic_top10": ["asi02"],
                    "mitre_atlas": ["AML.T0051"],
                }
            }
        },
    }
    sarif = to_sarif([Finding("GHSA-X", ref, "high")], {"GHSA-X": advisory})
    props = sarif["runs"][0]["results"][0]["properties"]
    assert props["taxonomies"] == {
        "owasp_agentic_top10": ["asi02"],
        "mitre_atlas": ["AML.T0051"],
    }


def test_sarif_omits_taxonomies_without_overlay():
    ref = ComponentRef(
        ecosystem="npm",
        name="pkg",
        version="1.0.0",
        source_manifest="package.json",
    )
    sarif = to_sarif([Finding("GHSA-X", ref, "high")], {})
    props = sarif["runs"][0]["results"][0].get("properties", {})
    assert "taxonomies" not in props
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `uv run pytest tests/test_sarif.py -k taxonom -v`

Expected: FAIL — `KeyError: 'taxonomies'` on the first test.

- [ ] **Step 3: Implement.**

In `tools/sarif.py`, inside `_properties_for`, after the `attributed_to` block
and before the `extra = finding.component.extra or {}` line:

```python
    taxonomies = output.get("taxonomies")
    if isinstance(taxonomies, dict) and taxonomies:
        props["taxonomies"] = taxonomies
```

Read it off `output` rather than calling `overlay_taxonomies(advisory)` again —
`output` is already the normalized envelope, so there is one extraction per
finding and one place where the shape is decided.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `uv run pytest tests/test_sarif.py -v`

Expected: PASS, whole file.

- [ ] **Step 5: Run the full gate.**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`

Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add tools/sarif.py tests/test_sarif.py
git commit -m "feat(sarif): carry overlay taxonomies in result properties"
```

---

## Task 6: Cross-layer e2e test

The test CLAUDE.md requires: it fails if the corpus loader, the matcher, or the
renderer regresses, and no single module test covers that wiring.

`tests/conftest.py` installs an autouse offline OSV fixture
(`_offline_osv_for_scan_tests`), so this makes no network call.

`overlays/GHSA-3q26-f695-pp76.yaml` is the real corpus record used here. It
carries `owasp_agentic_top10: [asi02, asi05]` and `evidence_level: confirmed`.

**Files:**
- Modify: `tests/test_e2e.py`

**Interfaces:**
- Consumes: the default-card behavior from Task 3, end to end through the CLI.
- Produces: nothing.

- [ ] **Step 1: Write the failing test.**

Append to `tests/test_e2e.py`, after
`test_repo_lockfile_finds_corpus_advisory`:

```python
def test_default_scan_text_shows_agentic_taxonomy_from_real_corpus(tmp_path):
    """Corpus overlay -> matcher -> default text card, without -v.

    Fails if the overlay loader stops merging `database_specific.openaca`, if the
    matcher stops attaching the advisory, or if the renderer re-gates the agentic
    line behind verbose. Per ADR-0043 the default card shows only the agentic
    family.
    """
    from tools.scan import main as scan_main

    target = tmp_path / "host-repo"
    target.mkdir()
    _mark_as_plugin(target, name="host", version="1.0.0")
    (target / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "host", "version": "1.0.0"},
                    "node_modules/@cyanheads/git-mcp-server": {"version": "1.1.0"},
                },
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(scan_main, ["repo", "--target", str(target)])

    assert result.exit_code == 1, result.output
    assert "GHSA-3q26-f695-pp76" in result.output
    assert "owasp-asi: ASI02, ASI05  [owasp-agentic-top-10-2026]" in result.output
```

- [ ] **Step 2: Run the test, then confirm it has teeth.**

Run: `uv run pytest tests/test_e2e.py -k agentic_taxonomy_from_real_corpus -v`

Expected: PASS, because Task 3 already landed. A test that has never been seen
red proves nothing, so to confirm it actually guards the behavior,
temporarily re-gate the `owasp-asi` line in `tools/render.py` behind
`if verbose and agentic:`, re-run, and see it FAIL on the
`"owasp-asi: ASI02, ASI05  [...]"` assertion. Then revert that edit and re-run to
green. Do not commit the temporary edit.

- [ ] **Step 3: Run the full gate.**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`

Expected: all green.

- [ ] **Step 4: Commit.**

```bash
git add tests/test_e2e.py
git commit -m "test(e2e): default scan text carries the agentic taxonomy"
```

---

## Task 7: Verify and open the PR

**Files:** none.

**Interfaces:** none.

- [ ] **Step 1: Install the pre-push hook if it is not already installed.**

Run: `ls .git/hooks/pre-push 2>/dev/null || bash scripts/install-hooks.sh`

The hook runs the same lint / type / test commands CI runs. It was not installed
on this clone as of 2026-08-10.

- [ ] **Step 2: Run the full CI-parity gate one final time.**

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest --cov=tools --cov-report=term-missing
uv run openaca lint overlays/
uv run openaca lint capabilities/
```

Expected: all green. Paste the output — per
`superpowers:verification-before-completion`, no completion claim without it.

- [ ] **Step 3: Eyeball the actual output.**

```bash
uv run openaca scan repo --target . --fail-on none
```

Expected: findings with overlays show an `owasp-asi:` line; findings without one
do not. Confirm the card still reads well and the line lands under the right
finding.

- [ ] **Step 4: Push and open the PR.**

```bash
git push -u origin feat/default-agentic-taxonomy-surfacing
```

Then open a ready PR whose body links `docs/specs/default-taxonomy-surfacing.md`
and ADR-0043, and states in one paragraph that the JSON/SARIF change extends
ADR-0035's taxonomy-travel principle to a surface it did not itself decide,
while the display-subset choice is the new decision recorded in ADR-0043.

Confirm with the user before opening the PR — CLAUDE.md requires it when the
request did not explicitly ask for one.
