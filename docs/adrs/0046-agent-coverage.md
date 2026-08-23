---
id: 0046
title: Express agent scan completeness with the capability coverage vocabulary
status: accepted
date: 2026-08-22
supersedes: null
superseded-by: null
---

## Context

Not every agent runtime's composition is fully observable from what it writes to
disk or exposes over an API. Three distinct gaps show up in practice: enable state
that lives outside any file, components registered by running code and never
written down, and composition built by a setup script after a scan has run.

Without a way to say so, a BOM listing three MCP servers reads as authoritative —
and a reader cannot tell whether the risk is that some of those three are
switched off, or that a fourth exists and is missing.

ADR-0041 already solved this shape for capabilities: a `capability_coverage`
marker of `unknown | partial | complete`, governed by rule 2, *"absence is not
falsehood; declining beats guessing."* Agent coverage answers the same shape of
question at a different scope, so the vocabulary is reused rather than duplicated
— two vocabularies for "we cannot see everything" is the drift `CLAUDE.md`
forbids.

## Decision

An agent carries `openaca:composition_coverage` using ADR-0041's three levels —
`unknown`, `partial`, `complete`. Only the level is emitted; named reasons are
deferred (see the alternatives below).

**The name is qualified deliberately.** An unqualified `openaca:coverage` would
sit in one document beside `openaca:capability_coverage`, drawn from the same
three levels, meaning something else entirely. `composition_coverage` says which
subject it measures at the point of reading, rather than leaving a reader to infer
it from where the property is attached.

**What is reused, precisely.** The three level names and the governing principle,
and nothing else. Composition coverage is a **new, separate property at a
different scope**: `openaca:capability_coverage` is per *component* and answers
"is this component's capability list complete?", while composition coverage is per
*agent* and answers "is this agent's composition complete?" Consequences of that
split:

- The linter's capabilities/coverage pairing rule is specific to capability
  coverage and does not transfer.
- Reasons are new. `capability_coverage` has no reason concept at all today, so
  this is defining a vocabulary rather than extending one.
- `complete` becomes reachable for the first time. `capabilities_for_ref` emits
  only `unknown` or `partial`; the third level is declared in `COVERAGE_LEVELS`
  but no current code path produces it. An agent claiming `complete` is a value no
  consumer or linter has yet seen in practice.

**Coverage measures discovery, not matchability.** A component that can be seen
but not resolved to an advisory coordinate is fully discovered: a remote MCP
server is inventoried with no OSV federation at all (ADR-0020) and its agent is
not thereby `partial`. Nor is the implementation behind a declared tool part of
the composition — a managed service's action group is a component because the
agent can call it, while the function it invokes is a separate artifact, as an MCP
server's source code is. A fourth gap for that case — "opaque backing resource" —
is deliberately **not** defined: it would measure matchability and mislabel a
fully-discovered managed agent as partial.

**Only the level is emitted.** Three distinct gaps produce a `partial` verdict —
activation unobservable, registration unobservable, materialized at runtime — and
they differ in whether they cause over- or under-reporting. A kind spec must state
which apply to each of its surfaces, and the level is derived from them. But they are **not** published as a BOM property:

- They are **derivable**. Coverage is a function of (kind, composition source),
  and a BOM
  carries both, so a consumer that knows the kind knows why it is partial. This
  is the same test that removed `openaca:agent_host` and declined a stored
  definition digest.
- The vocabulary is **knowingly incomplete** — see *When to revisit*. Publishing
  it now would freeze a taxonomy with a known hole.

Adding a reasons property later is additive and breaks no consumer, so waiting is
cheap while publishing a wrong vocabulary is not.

`complete` is claimable only when every surface is file- or API-declared.

Coverage is resolved **per composition source**, not fixed per kind. A runtime
whose enable state is unobservable once installed has nothing installed to toggle
when it is only declared in a repo, so the same kind carries different coverage
for each source. *Where* it is installed makes no difference: the same runtime is
blind to the same things in a sandbox as on a workstation.

## Alternatives considered

- **A bespoke verifiability vocabulary** for agents — rejected as parallel-
  taxonomy drift. It would leave the project with two vocabularies for "we cannot
  see everything," which is the same failure the no-parallel-severity rule in
  `CLAUDE.md` exists to prevent.
- **A single boolean `complete` flag** — rejected. `unknown` and `partial` are
  different states: one says no assessment was made, the other says an assessment
  found a gap. A boolean collapses them.
- **Publishing the reasons as a BOM property now** — rejected as premature, not as
  wrong. It is derivable from (kind, composition source) while one kind exists,
  and the
  vocabulary has a known hole, so it would be a published contract we already
  expect to change. It remains the natural next addition once the hole is closed.
- **Coverage fixed per kind** — rejected. It would be wrong for one composition
  source or the other for any runtime whose observability differs between being
  installed and being merely declared, and silently so.
- **Omitting coverage and documenting the gaps in prose** — rejected. A gap that
  is not on the record travels no further than the spec; renderers and downstream
  consumers need it on the document to avoid presenting a partial BOM as complete.

## Consequences

Renderers can state completeness without any per-kind special-casing, and a
partial BOM cannot silently read as authoritative. Because only the level is
emitted, a consumer sees *that* observability is incomplete but not why —
distinguishing "some of these may be off" from "there may be more than these"
waits on the deferred reasons property.

Recording the reasons surfaced a result worth keeping: managed agents are *more*
statically observable than framework agents, because a vendor's declarative record
beats tools bound in code. Code defeats us, not remoteness — the opposite of the
usual intuition that a remote managed service is less legible than a local file.

Cost: every kind must declare coverage for both composition sources, which is
one more thing to get right per surface. A wrongly-claimed `complete` is worse
than a cautious `partial`, so the default when unsure is `partial`.

## When to revisit

**Emit the reasons once the vocabulary is whole.** A surface a runtime declares
but this scanner does not yet parse fits none of the three gaps: the components
are declared, are not toggled elsewhere, and are not built at runtime. The gap is
scanner maturity, and all three gaps are runtime properties.

That does not affect the level, which is `partial` — a kind must never claim
`complete` for a composition source whose surfaces it does not fully
parse. It affects only
how the reason would be *expressed*, which is why deferring the reasons property
also defers this. Closing it means choosing between a fourth gap
(`surface_not_parsed`) and a separate scanner-gap marker; the distinction matters
because one field cannot tell a consumer whether to wait for a scanner release or
accept permanent blindness. Publishing the reasons will amend this ADR.

Beyond that: if a further reason is needed that is not a special case of the
three, the taxonomy is probably the wrong shape and should be reconsidered rather
than extended.

Also revisit if runtime attestation — observing an agent's actual loaded
composition rather than its declaration — ever makes `complete` claimable for a
code-defined runtime.
