---
id: 0045
title: Give an agent its own kind property; key it on asset, kind, and agent id
status: accepted
date: 2026-08-22
supersedes: null
superseded-by: null
---

## Context

ADR-0044 makes the agent the BOM root, so an agent now needs a key. Two
questions had to be answered: which property carries the runtime kind, and what
uniquely identifies one agent across scans and time.

The intuitive choice is `openaca:identity` as `agent/{kind id}`. It is wrong, and
the reason is worth recording because the mistake is natural.
`openaca:identity` means one thing throughout the format (ADR-0038/0042): *a
specific logical component*, stable across manifests, scans, and time.
`mcp-server/playwright` on fifty machines is one logical component with fifty
occurrences. A runtime kind is not that: two Cursor agents are not two
occurrences of one agent, they are two *different* agents that share a type, one
with three MCP servers and one with seven. Putting the kind in the identity field
makes that field mean "a specific artifact" on a component and "a category" on
an agent.

The counter-argument — that ADR-0038 already defines identity as a deliberately
*shared* key, so a kind repeated across agents fits the existing pattern — is
coherent but relies on "logical component" stretching to cover "category of
runtime." What settles it is that under ADR-0044 the agent is
`metadata.component`, not a row in `components[]`. It therefore never lands in
the same table or the same group-by as component identities, so reusing the field
buys nothing.

A second constraint comes from `_make_normalizer` in `tools/graph_build.py`. Node
keys become CycloneDX `bom-ref`s and must be reproducible across machines, which
is what lets two machines running the same configuration be recognised as such.
Anything machine-specific in a key destroys that property.

## Decision

An agent carries its runtime kind in its own property, `openaca:agent_kind`.
`openaca:identity` keeps exactly one meaning format-wide and is not used for
agents.

An agent's **instance key spans two layers**: the registration envelope's asset
external id says *which* place it came from, and the document says *what sort of*
agent it is. The key is therefore **(asset, kind, agent id)**.

The composition source is deliberately **not** part of that key. It is
descriptive — the asset already implies the place — which is precisely why the
categorical source is safe to carry in a document while place identity is not.

The agent id is required only for kinds with same-kind multiplicity, where asset
plus kind alone would not resolve to one agent.

**Cardinality itself is not BOM vocabulary.** It is a scanner-internal
declaration on a kind, never a property and never a word a consumer needs; its
only role is deciding whether that kind emits an agent id. One consequence
follows: the present-iff-multiplicity rule needs the kind registry, so it is a
scanner self-check that happens to run in the linter rather than something the
format can express. It is kept because a singleton kind emitting a discriminator
means discovery is wrong. Cardinality is also declared per kind rather than
inferred from how many instances a discovery happened to return.

It is named for an **id** rather than a name because it is part of the key that
drift pairs on. Where a kind's surface exposes both an immutable identifier and a
renameable display label — Bedrock's `agentId` beside its `agentName` — the id is
the only correct source, or renaming an agent would move its key and the diff
would report a delete plus an add for an unchanged composition. Where a surface
exposes one string, as LangGraph's `graphs` map key does, that string is both and
there is nothing to choose.

The display label belongs in the standard CycloneDX `metadata.component.name`,
which every component carries anyway — so Bedrock keys on `ABCDEFGHIJ` while a
reader sees `payments-triage`, and a rename changes the latter without moving the
former. A singleton kind has no discriminator, so its `name` is the kind's own
display label. `name` is never an identifier: `bom-ref` and the agent id are.
Qualifying it as `{kind}/{agent id}` was rejected — `bom-ref` and the output
filename already carry that pair, and the kind is in the document for any
consumer that wants to compose a qualified label.

The tempting formulation — the triple *(kind, source, agent id)*, all
three read from the document — is not unique: two workstations running the same
runtime with the same configuration produce identical values for all three, since
a document carries no place identity. Uniqueness comes from the envelope, which is
why the key spans both layers.

The agent's `bom-ref` remains its within-document key, per ADR-0037's
node-key-is-the-bom-ref invariant, and is prefixed **`root/`**. Not `agent/`: the
closed component-type set already uses `agent` for a subagent, whose identity is
`agent/reviewer`. A subject keyed `agent/claude-code` would sit in that same
namespace, and the prefix is load-bearing — a `startswith` test on it decides
whether a stored BOM is graph-backed, and misreading that silently drops
agent-dependency findings. `root/` is unambiguous against it.

## Alternatives considered

- **Put the kind in `openaca:identity` as `agent/{kind id}`** — rejected, as
  above: it gives one field two meanings, and the reuse buys nothing because an
  agent is `metadata.component` rather than a `components[]` row.
- **A kind property *and* a kind-shaped identity** — rejected as a second
  encoding of one fact, the same defect that removed `openaca:agent_host` (which
  was mechanically derived from `runtime_hosts` and read only by a test).
- **Fold the agent id into the kind** (`{kind}/{id}`) — rejected. It
  makes the kind property per-instance rather than per-class, so "every agent of
  this runtime in the fleet" stops being a single equality test, and it is
  internally inconsistent: singleton kinds would carry no suffix while
  many-per-place kinds would.
- **Qualify the kind by where it runs** (`{kind}@{place}`) — rejected. It makes
  node keys machine-dependent, destroying recognition of two machines running
  identical configurations, and it splits the declared and installed views of one
  configuration into unrelated key spaces — losing "does this machine run what
  the repo declares" as a comparison. It also still fails to disambiguate many
  agents of one kind in one place.
- **A stored definition digest** over the composition, so identical
  configurations are recognisable — rejected as derivable from the component set.
  It would also need the same post-redaction recomputation `content_hash` already
  gets, and would commit the scanner and backend to one canonicalisation forever,
  where a bug silently mis-groups configurations.
- **An agent-qualified node-key prefix** (`agent/{kind}/<rel>`) — rejected. The
  path label exists to keep roots from colliding *within one scan* (install root
  versus project root), not to separate kinds, and each BOM is already
  single-kind, so the agent adds nothing while renaming every `bom-ref`. Root
  labels are named for the kind that **owns the root** instead, which also fixes
  a pre-existing gap: a file one runtime compat-reads from another's config root
  is under neither its own install root nor the project, and currently falls
  through to an absolute path. Owner-named labels also mean a shared file keys
  *identically* in both agents' BOMs, which is only safe because each agent is a
  separate graph and a separate document — `Graph.validate` would reject one node
  with two agent parents, and `bom-ref` need only be unique within a document.
- **Place identity as a BOM property** — rejected on the privacy contract. A
  hostname, cloud account, or cluster identifier is exactly what the redaction
  pass strips from BOM content; machine identity already travels in the
  registration envelope as the asset's external id, and place identity belongs
  there too. Only the categorical composition source is safe in the document.

## Consequences

`openaca:identity` retains a single meaning across components and agents alike,
so a consumer grouping on it never has to ask what sort of thing it is looking
at. "Every agent of this runtime in the fleet" is an equality test on
`openaca:agent_kind`.

The key is what drift pairs on: to establish that one of fourteen agents
changed, a consumer matches the previous scan's BOM to the current one on the
key, then diffs components on `bom-ref`. Two machines with byte-identical
configuration still produce identical node keys, preserving the dedup property
the path normaliser exists to provide, and the declared and installed views of one
configuration differ only in their `openaca:composition_source`.

Costs. This is one more property on the metadata component, where the earlier
draft had none — justified because nothing else carries the kind once identity is
ruled out. And BOM diffing gains a pairing step it does not have today: diffs
currently match components on `bom-ref` within a single document pair, so with
many BOMs per scan something must first pair BOMs across scans on that key.

## When to revisit

If agents ever become rows in `components[]` rather than the document's metadata
component — the shape ADR-0044 records as its own fallback — the argument for a
separate property weakens, because agent and component keys would then share a
table and a group-by.
