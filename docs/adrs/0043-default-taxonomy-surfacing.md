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
