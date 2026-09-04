---
id: 0051
title: Extend the upload redaction contract to BOM metadata
status: superseded
date: 2026-08-24
supersedes: null
superseded-by: 0063
---

## Context

ADR-0003 draws the redaction boundary at upload: the OSS CLI runs on the user's own
machine, so a local BOM may carry absolute filesystem paths, but an upload crosses into
a multi-tenant store and must not.

Three layers enforce it — the collector's `_redact_payload_for_remote`, the local
`enforce_remote_upload_contract`, and the hosted-side validator. All three walk
`bom.components[*].properties`, scan the `openaca:*` values, and stop. **None of them
looks at `bom.metadata`.**

That has been safe by accident. The only metadata value the collector writes today is
`openaca:target`, and on the upload path it is a literal the collector constructs
itself. Nothing machine-derived has ever been in there.

The collector's agent-rooted migration ends that: `metadata.component.properties` starts
carrying the agent's own properties, including `openaca:agent_id` and the display label
`metadata.component.name`. `docs/specs/multi-agent-support.md` is explicit that the
scanner's schema **does not constrain the agent id**, and a kind with same-kind
multiplicity draws it from whatever the surface provides — a developer-chosen map key, a
provider-assigned identifier, a path.

Today that is still safe, and still only by accident: the one shipped kind is a
singleton, so `agent_id` is absent and the label is the literal `Claude Code`. The same
accident, one layer over. The difference is that `bom.metadata` is no longer a block the
collector fills entirely with its own constants.

## Decision

The upload redaction contract covers, in addition to `bom.components[*].properties`:

- `bom.metadata.properties` — `openaca:*` values, same rules, same prefix filter.
- `bom.metadata.component.properties` — likewise.
- `bom.metadata.component.name` — the collector-synthesized agent display label, which
  carries **no** `openaca:` prefix and is therefore covered by name rather than by
  filter.

Both the collector's redaction pass and the local contract enforcer apply it, and it
lands **before** the collector writes anything into metadata.

The scope rule is therefore *"every string in `bom.metadata` that the collector
synthesizes"*, not *"every `openaca:*` property in `bom.metadata`"*. The prefix filter
remains how the property lists are selected; it is not the definition of the boundary.

## Alternatives considered

- **Leave the scope alone and forbid the collector from writing machine-derived metadata**
  — rejected because it enforces a data rule with a coding convention. It holds exactly
  as long as every future author knows it, and the value most likely to break it
  (`openaca:target`) is one the local scan path already fills with an absolute path.
  The check belongs where the data crosses the boundary.
- **Redact only `metadata.component.properties`**, since that is what this change adds —
  rejected as fixing the instance rather than the gap. `metadata.properties` is the
  older of the two and holds the riskier value.
- **Cover only `openaca:*` properties and leave `metadata.component.name` out** — this
  was the first version of this decision, and it was wrong. The name is emitted from
  `AgentInstance.display_name`, typed as an unconstrained `str`, so a future kind can put
  anything there — including an absolute path — and no layer would catch it. It is safe
  today only because the one shipped kind hardcodes the literal `Claude Code`: the same
  "safe by accident" this ADR exists to end. It also left the contract and
  `docs/specs/collector-agent-rooted-uploads.md`'s whole-metadata invariant saying
  different things, which is how the gap survived review three rounds running.
- **Narrow the spec's invariant to the property lists instead** — rejected as the weaker
  half of the same fork. It would resolve the contradiction by writing down that a
  machine-derived string is knowingly left unchecked, and buys nothing: the fix is one
  string.
- **Rely on the hosted-side validator** — rejected because it has the same blind spot,
  and because a violation caught server-side has already crossed the network. It is also
  the layer the collector cannot fix from here.
- **Scan the whole document indiscriminately** — rejected because it would scan
  pass-through CycloneDX content the collector did not synthesize, which is outside the
  contract's scope and would reject legitimate upstream data.

## Consequences

Absolute paths, `file://` URIs, and URLs carrying paths or credentials are redacted
wherever the collector writes them, and the local enforcer fails with the same message
it already gives for components. The two property lists reuse the existing property-list
walk; `metadata.component.name` is a single string and calls the same value-level rule
directly.

Redacting a display label risks mangling a legitimate name, so the rule's precision
matters: `_redact_property_value_for_remote` acts only on absolute paths, `file://`
URIs, URLs with a path or userinfo, and embedded Unix paths whose `/` follows a
non-word character. A name like `my-org/agent` is untouched, while
`/Users/alex/agents/a` is caught. A kind whose display label is deliberately
path-shaped would see it redacted — the correct trade at an upload boundary.

This slightly widens what the local enforcer rejects, so a payload that would previously
have uploaded now fails locally. That is the intended direction — no such payload exists
today, since the only metadata value is a literal.

The most concrete leak this originally guarded against no longer exists. An earlier draft
of the collector migration kept `openaca:target` on the upload path, where porting the
scan call site's `target=str(agent.config_root)` would have uploaded an absolute home
path past all three layers. That property is now dropped from uploads entirely, so the
vector is gone. The scope extension outlives it because the reason was never that one
value: it is that the collector no longer authors every string in `bom.metadata`.

Two limits worth naming. The hosted validator still has the blind spot; closing it there
is the hosted side's change, and until then the local layers are the only enforcement.
And a stale offline-cache payload written before this ships is validated on replay by
the new enforcer, so it can now be rejected rather than uploaded — correct, but it means
an upgrade can discard a cached payload that a previous version would have sent.

## When to revisit

- If the collector ever synthesizes a `bom.metadata` string beyond the three locations
  named in the Decision, the enumeration stops being sufficient. This ADR's first
  version listed only the property lists and missed `metadata.component.name` for
  exactly that reason — the boundary is "what the collector authors", and the list has
  to be re-derived from the emitter whenever the emitter grows a field.
- If the collector ever forwards pass-through CycloneDX content authored elsewhere, the
  "only what we synthesized" scope needs restating rather than widening by default.
