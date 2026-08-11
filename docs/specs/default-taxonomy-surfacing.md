# Default Taxonomy Surfacing In Scan Output

## Goal

Overlay taxonomies are loaded on every scan and printed almost nowhere.

The scanner merges each bundled overlay into the OSV record it returns, so
`database_specific.openaca.taxonomies` is present in memory for the whole run.
Only two surfaces render it: `-v` text output and the static HTML site. The
default text card, the JSON envelope, and SARIF all drop it.

This spec makes the OWASP Agentic Top 10 mapping visible in default scan output
and carries the full taxonomy block through the machine formats.

## Current Behavior

| Surface | Renders taxonomies? | Where |
|---|---|---|
| Text, default card | No | — |
| Text, `-v` | Yes, all list-valued families | `tools/render.py:434` (inside `if verbose:`) |
| JSON envelope | No | `tools/finding_output.py:130` builds the vulnerability output with no taxonomy key |
| SARIF | No | `tools/sarif.py:69` opens the `openaca` block but reads only `source` and `overlay_source` |
| Static site | Yes, all list-valued families | `tools/templates/advisory.html.j2:79` |

The asymmetry is sharper than "a field is missing." Posture findings already do
what this spec asks for:

- `tools/render.py:565` prints `standards:` in the default card, no verbose gate.
- `tools/finding_output.py:178` puts `standards` in the JSON envelope.
- `tools/sarif.py:183` puts `standards` in SARIF result properties.
- `Standards` has an `owasp_agentic_top10` field (`tools/posture/finding.py:32`).

So a posture rule reports its ASI code by default, while a matched advisory
carrying the same ASI code hides it behind `-v`. The two finding families
disagree about whether agent-context taxonomy is default-visible information.

## Relationship To ADR-0035

This extends an existing principle to a new surface; it is not a fix for a
prior violation of one.

**ADR-0035** (accepted, in force) states that on every finding family,
"`source_version`, `confidence`, `evidence`, and taxonomy fields travel with the
finding where available." That clause is about attribution-adjacent metadata
crossing the external-scanner adapter boundary, not about which output
surfaces render advisory overlay data. Surfacing `taxonomies` in JSON and SARIF
for vulnerability findings is consistent with that same principle, even though
the field is read from the advisory record at output time rather than stamped
onto the `Finding` dataclass (see the rejected alternative in ADR-0043).

**ADR-0012** is supportive. It removed `component_type`, `surfaces`, and
`agent_impact` from canonical overlays and deliberately kept `taxonomies` and
`evidence_level`. Its Consequences anticipate this spec directly: reports
"combine local scan observations with upstream advisory data and
OpenACA-reviewed taxonomy mappings." The "renderers must stop displaying"
instruction in ADR-0012 applies to the removed fields, not to taxonomies.

**ADR-0016** is extended, not contradicted. Its Consequences enumerate the JSON
envelope field by field — `finding_type`, `component`, `component.source`,
`active_in`, `declared_by`, `component_path`, `matched_advisory` for
vulnerability findings, `rule_id` and `standards` for posture findings. Adding a
top-level `taxonomies` key extends that enumeration. ADR-0016 does not declare
the envelope closed, so this amends rather than supersedes, following the
ADR-0041-amends-ADR-0022 precedent.

## Corpus Shape

Measured across the 107 bundled overlays on 2026-08-10:

| Family | Overlays carrying it |
|---|---|
| `owasp_agentic_top10` | 107 (100%) |
| `owasp_llm_top10` | 101 |
| `mitre_atlas` | 101 |
| `owasp_mcp_top10` | 71 |

Rendering every family in the default card costs up to four extra lines per
finding. With ten findings that is roughly forty added lines, which buries the
severity and fix lines the card exists to surface. `owasp_agentic_top10` is the
only family with full coverage and the one that makes OpenACA's agent-context
claim concrete, so it is the family the default card shows.

Most findings will carry no overlay at all: the corpus is 107 records while
OSV.dev returns advisories for anything matched.

## Design

### 1. Shared extractor

Add to `tools/finding_output.py`, alongside the existing normalizers:

```python
def overlay_taxonomies(advisory: dict | None) -> dict[str, list[str]]:
    """Extract database_specific.openaca.taxonomies from an advisory record."""
```

Returns `{}` for a missing advisory, a missing or non-dict `database_specific`,
a missing or non-dict `openaca` block, or absent taxonomies. Drops any family
whose value is not a non-empty list, mirroring `Standards.to_dict()`; list
members are coerced to `str`, matching the existing verbose renderer. No new
module.

Three consumers call it: the text renderer, `finding_to_output`, and — free,
since `_properties_for` already calls `finding_to_output` — SARIF. The
`isinstance`-guarded walk currently inlined at `tools/render.py:435-445` stops
being duplicated per renderer.

Rejected alternatives:

- **Stamp taxonomies onto the `Finding` dataclass at match time.** Cleaner call
  sites, but it copies overlay data onto the finding when it already lives in
  `advisory_index`, creating two sources of truth for one fact. ADR-0012 keeps
  overlay content in the advisory record.
- **Widen the posture `Standards` dataclass and reuse it for advisories.** One
  vocabulary for both families, but `Standards` carries `cwe`,
  `openssf_scorecard`, and `slsa` that overlays never populate, and lacks
  `mitre_atlas` and `owasp_llm_top10` that overlays do. Widening a frozen
  dataclass constructed by five posture rules is a larger blast radius than the
  problem justifies, and it blurs a distinction ADR-0035 draws deliberately:
  posture standards are rule-authored, overlay taxonomies are corpus-authored.

### 2. Default text card

In `_render_finding_groups` (`tools/render.py:434`), outside the `verbose`
gate, emit one line per finding when `owasp_agentic_top10` is non-empty:

```
  HIGH  GHSA-xxxx-yyyy-zzzz  fixed in 1.2.4  Tool poisoning via ...  [osv.dev]
        owasp-asi: ASI02, ASI06  [owasp-agentic-top-10-2026]
```

When the advisory has no overlay, or the overlay carries taxonomies but no
`owasp_agentic_top10`, the line is omitted with no placeholder. Absence of the
line means no agentic mapping, consistent with how `fix:` already degrades.

Under `-v`, the existing block still prints `evidence_level`, `confidence`, and
identity details, and the generic `taxonomies:` line still prints the other
families — but no longer repeats `owasp_agentic_top10`, since the default line
above already showed it. `-v` becomes strictly additive rather than a superset
that duplicates its own default.

Both the card path and the legacy path route through `_render_finding_groups`,
so this is one edit covering both.

### 3. JSON envelope

`finding_to_output()` gains a `taxonomies` key carrying **all list-valued**
families (`supplemental_taxonomies` is excluded because it is an object of
arrays, not a list, pending a shape decision), omitted entirely when the
extractor returns `{}`:

```json
{
  "finding_type": "vulnerability",
  "id": "GHSA-xxxx-yyyy-zzzz",
  "matched_advisory": { "id": "GHSA-xxxx-yyyy-zzzz", "aliases": ["CVE-2026-1234"] },
  "taxonomies": {
    "owasp_agentic_top10": ["asi02"],
    "mitre_atlas": ["AML.T0051"]
  }
}
```

The key is `taxonomies`, not `standards`. It matches the overlay schema key and
ADR-0035's own phrasing ("taxonomy fields"), and keeping it distinct from
posture's `standards` preserves the rule-authored / corpus-authored provenance
difference at the field level.

Machine formats carry all list-valued families because they have no noise
budget; the display subset is a text-card concern only.

### 4. SARIF

`_properties_for` (`tools/sarif.py:69-79`) already opens the `openaca` block for
`source` and `overlay_source`. It reads `taxonomies` in the same pass and sets
`properties.taxonomies`, same shape as the JSON envelope and same shape posture
results already emit at `tools/sarif.py:183`.

### 5. ADR-0043

One ADR, scoped narrowly. It records the display-subset decision — why the
default card shows only `owasp_agentic_top10` while JSON and SARIF carry all
list-valued families (`supplemental_taxonomies` is excluded pending a shape
decision) — because the rejected alternative (all families everywhere) is
plausible, will be re-suggested, and its reason (card noise budget) is invisible
in the diff. It cites ADR-0035 as consistent precedent for the machine-format
work and notes that it amends ADR-0016's enumerated envelope.

The field-name choice (`taxonomies` over `standards`) is recorded in the same
ADR as a secondary note. The silent-omission behavior needs no ADR; it is
obvious from the code.

ADR-0043 is written and merged before implementation begins.

## Testing

TDD: failing test first for each behavior.

**`tests/test_render.py`**

- Default card renders `owasp-asi:` for a finding whose advisory carries
  `owasp_agentic_top10`.
- `-v` renders all other families plus `evidence_level` and `confidence`, and
  does not repeat `owasp_agentic_top10` in the generic `taxonomies:` line.
- Advisory with no overlay produces no `owasp-asi:` line.
- Overlay with taxonomies but no `owasp_agentic_top10` produces no `owasp-asi:`
  line.

**`tests/test_scan.py`**

- `taxonomies` present in the JSON envelope with all list-valued families.
- Key absent entirely when the advisory has no overlay.

**`tests/test_sarif.py`**

- `properties.taxonomies` populated for an overlaid finding.

**`tests/test_e2e.py`**

The cross-layer test CLAUDE.md requires. A real overlay from `overlays/` plus a
fixture manifest, scanned at default verbosity, produces text containing the
`owasp-asi:` line. This fails if the corpus loader, the matcher, or the renderer
regresses — no single module test covers that wiring.

## Out Of Scope

- **Summary rollup.** An aggregate ASI-coverage line in the Summary section was
  considered and rejected for this change; per-finding omission is the chosen
  behavior when no overlay exists.
- **Remote upload payload.** `_finding_taxonomies` at
  `tools/remote/collector.py:808` is a different concept — it carries
  `openaca_categories`, not overlay taxonomies. Changing it would widen the
  upload contract in `tools/remote/upload_contract.py` and belongs to its own
  decision.
- **`openaca.core` facade.** `finding_output` is not exported by
  `openaca/core/__init__.py`, so the ADR-0028 facade is untouched. Consumers
  read the JSON envelope directly.
- **Static site.** `advisory.html.j2` already renders all five list-valued
  families correctly; `supplemental_taxonomies` is unrendered there too,
  pending the same shape decision.
- **Triage and exposure reports.** Out of band; ADR-0040 keeps that a separate
  consumption layer over scan evidence.
