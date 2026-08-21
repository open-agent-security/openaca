---
id: 0047
title: BOM host provenance is unconditional; renderers never infer host from components
status: accepted
date: 2026-08-20
supersedes: null
superseded-by: null
---

## Context

Multi-host support (ADR-0044/0045) wrote `openaca:scanned_hosts` and
`openaca:host_config_roots` onto BOM metadata only when 2+ hosts were
selected, so that single-host BOMs stayed byte-identical to pre-multi-host
output. Everything downstream that needed a host list therefore had to
*infer* one when the property was absent: `hosts_from_refs()` derives it
from the components' own `runtime_hosts`, falling back to `claude-code`
when there is nothing to derive from.

That inference is not equivalent to the record it replaced, and the Codex
review on PR #158 found six separate symptoms of the gap across four
rounds — each fixed at its own call site, each followed by another
instance:

- An empty Cursor endpoint BOM reported `components_by_host` as
  `{"claude-code": 0}`. With zero components there is no attribution to
  infer from, so ingestion named a host that was never scanned.
- A repo BOM whose components were all Claude Code's reconstructed as a
  Claude-Code-only scan and dropped the `[claude-code]` tags a live
  `scan repo` of the identical tree shows, because inference cannot
  distinguish "only claude-code was scanned" from "both hosts were
  scanned and only claude-code had components."
- A Cursor-only endpoint BOM rendered with no host anywhere in its text
  card: the tree's `[<host>]` tags are 2+-host-gated, and a BOM's Target
  block says `Agent BOM` where a live endpoint scan says
  `host surface: Cursor`.

The common cause is that host selection is scan-time knowledge that only
the writer has. Components are evidence of what was *found*, never of
what was *looked for*, and the two differ precisely in the cases that
matter — an empty result, or a host that contributed nothing.

## Decision

`openaca:scanned_hosts` is written on **every** BOM: `bom endpoint`
records its selection, `bom repo` records the full host registry it
always walks, and `remote sync` records the same for the wire BOM. Host
ids are registry constants, never paths, so this is redaction-safe.
Readers use the recorded list; `hosts_from_refs()` inference is retained
only as the legacy path for BOMs written before the property existed, and
absence now means "written before host awareness" rather than "single
host, guess which."

`openaca:host_config_roots` and the neutral `openaca:target` locator stay
2+-host-gated, for the reason they always were: a single-host selection
has exactly one authoritative config root, and `openaca:target` already
carries it.

Text output states host provenance wherever the surrounding card doesn't
already: the BOM card gains a `hosts` Target row when the selection isn't
exactly `[claude-code]` (live endpoint scans need none — their Target
block names the host surface; live repo scans need none — their tree tags
do it).

## Alternatives considered

- **Keep the multi-host gating and fix each renderer as it comes up** —
  rejected; that is the loop this ADR ends. Six review findings, four
  rounds, three modules, one cause. Inference at the read side cannot
  reconstruct information the write side declined to record, so every fix
  is local by construction and the next path finds the same hole.
- **Emit `scanned_hosts` only when the selection isn't the legacy
  default** (Codex's own narrower suggestion: "at least when it is not
  the legacy default") — rejected, though it does fix every reported
  symptom. It leaves "absent" meaning either "legacy writer" or
  "claude-code only", which is the same ambiguity one level down, and it
  makes the write rule conditional on a value rather than unconditional.
  A property that is always present is cheaper to reason about than one
  whose absence carries meaning.
- **Widen the tree's `[<host>]` tags to fire for any single non-default
  host** (the literal remedy the last finding suggested) — rejected as
  the primary fix. It addresses one rendering path, leaves the empty-BOM
  case unlabeled (no tree entries exist to tag), and would tag every line
  of a live single-host endpoint scan whose Target block already names
  the host. The Target row does the job once, including when the
  inventory is empty.
- **Preserve byte-identical single-host BOM output** (the constraint the
  gating existed to satisfy) — rejected as a promise not worth keeping.
  `openaca:scanned_hosts` and BOM `schema_version` 0.5 are both new in
  the same unreleased change, so there is no published single-host output
  to stay identical to; the only thing being preserved was a resemblance
  to 0.4 BOMs, at the cost of correctness in the empty case.

## Consequences

Every BOM gains one metadata property, and a `scan bom` round trip now
reproduces the host attribution of a live scan of the same target — pinned
end to end by `test_bom_round_trip_preserves_host_attribution_shown_by_a_live_scan`
and its Cursor-endpoint counterpart in `tests/test_e2e.py`. Empty BOMs
report the host that came up empty instead of the default one.

The costs: single-host BOMs are no longer byte-identical to pre-multi-host
output (accepted above); a claude-only BOM carries a property that merely
restates the default, which is the price of unconditional provenance; and
repo BOMs now round-trip with host tags visible, which is a deliberate
output change on the parity side, not a regression. `hosts_from_refs()`
survives as legacy-only code with no live writer producing input for it —
if it is ever deleted, old BOMs lose host attribution entirely, so it
should outlive at least one deprecation cycle.

## When to revisit

If `bom repo` gains a `--host` flag, its recorded value must become the
resolved selection rather than the full registry — the write site is
correct today only because repo mode has no selection to make. Revisit
the whole decision if BOM metadata ever becomes size-constrained enough
that unconditional properties matter, or if a host is added whose id is
not a safe constant to transmit.
