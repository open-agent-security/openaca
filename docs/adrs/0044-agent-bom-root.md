---
id: 0044
title: Make the agent the BOM root
status: accepted
date: 2026-08-22
supersedes: null
superseded-by: null
---

## Context

Every scan roots at a place. `scan endpoint` resolves one config directory and
`scan repo` walks one tree, each emitting exactly one BOM. Both assume a single
agent runtime: `_seed_endpoint` in `tools/graph_build.py` is hardcoded to Claude
Code, and `tools/parsers/__init__.py`'s flat `REGISTRY` has no runtime tagging.

Supporting a second runtime forces the question of what a BOM is *about*. Adding
one under the existing root would keep the place as the subject and make the
runtime a per-component annotation, which bakes in a second assumption rather
than removing the first.

So the root was evaluated against eighteen situations spanning both composition
sources (installed and declared), three kinds of place, and both cardinalities —
one agent of a kind in one place, and many. Managed and framework agents — AWS
Bedrock agents, LangGraph, CrewAI — were included as exemplars specifically so
the contract would not need re-rooting when one of them arrives.

The full evaluation, including the situation table this decision rests on, is in
`docs/specs/multi-agent-support.md`.

## Decision

A BOM describes one **agent**, not a place. A scan emits as many BOMs as
agents found, each carrying the agent as its `metadata.component`.

Construction builds **one graph per agent**, each with the agent as its single
target root, so attribution-by-containment (ADR-0037), precedence resolution, and
scope classification work unchanged — `scope_of`'s "an agent ancestor before the
target root" simply means "before the agent root." Emission is one BOM per graph.

Not one combined graph with agent nodes: `Graph.validate` rejects a node with
more than one parent and `Graph.root` requires exactly one target, so a file two
runtimes read — identical owner-derived key in both — would be a single node with
two agent parents, violating the invariant. Separate graphs preserve the tree and
keep that key identical across both BOMs, which is what makes the shared file
recognisable as one file.

Kind and composition source are independent axes. A **kind** is what reads the
composition, and two runtimes are the same kind if and only if they read the same
surface with the same schema; a kind is never qualified by where it runs.
**Composition source** says whether the composition was read from an agent that
exists (`installed`) or from a declaration of one (`declared`). Both are explicit
values, because declared results stay out of exposure counts and a missing
property would flip that.

Where an agent runs is deliberately *not* in the document. Distinguishing a
workstation from a sandbox from a managed service changes nothing a BOM decides —
remediation routing, drift continuity, asset keying, and criticality are all
asset-side facts the registration envelope already carries — and coverage, the one
BOM property the axis feeds, splits on installed-versus-declared rather than on
which place.

More than one kind may read the same file — one runtime reading another's
subagent directory, or a cross-tool convention several runtimes read as equals.
That needs no ownership or precedence concept between kinds: the file is a
component in each reading agent's BOM, sharing both identity and node key —
owner-named root labels (ADR-0045) mean the file keys identically in every
reading agent's document, which is only safe because each agent is a separate
graph and a separate document, exactly as ADR-0038 already provides for.

## Alternatives considered

- **One BOM per place, with each component tagged by which agents use it** —
  rejected. The agent is never an object, so an agent with nothing configured
  cannot be reported at all, and drift cannot localise to one agent out of many.
  Recording per-agent facts would need a parallel structure beside `components[]`
  with its own identity scheme and no edges. It also reintroduces the
  `attributed_to` property that ADR-0037 deliberately replaced with graph-derived
  attribution — a second encoding of what the edges already say.
- **One BOM per place, with the agent as a component row** — rejected as the
  emission boundary, and not viable as the internal graph shape either: a
  combined graph cannot hold a file two agents read without breaking the
  single-parent invariant. One document per
  place is unwieldy at two hundred agents in a single account, and any single
  agent's change diffs the whole document. It also forces the agent into
  `components[]`, requiring a CycloneDX component type for which none of
  `library`, `application`, or `framework` is honest — an agent is a configuration
  context, not software.
- **An ownership model for manifest patterns** — one kind is the origin of each
  path, a second kind reading it is a declared exception, and two kinds claiming
  one path is an error — rejected as machinery this decision makes unnecessary.
  Ownership is only needed while one BOM per place forces a component to be
  attributed to a single kind. With one BOM per agent there is no attribution to
  arbitrate: a file two runtimes read is a component in both their BOMs. A
  three-role scheme (owned, compat-read, shared) exists only to make the resulting
  collision rule behave on cross-tool conventions, which have no owner — so with no
  collision rule, the roles have no job.
- **Place-qualified kinds** — a separate kind for a runtime in the cloud —
  rejected. It duplicates every parser and every coverage declaration per place,
  for a distinction the composition source already carries where it matters.
- **A four-valued placement property** (`machine`, `ephemeral`,
  `production agent`, absent-means-declared) — rejected. Only the
  installed-versus-declared split changes anything the document decides, and
  `production agent` mixes durability with role, so a serverless managed agent
  cannot be expressed. It also role-qualifies a place, which this ADR's own
  kind-independence rule argues against. Adding finer values later is additive.
- **Deferring the root change and shipping a second kind first** — rejected. The
  property that cannot be retrofitted is discovery returning a *list*; a managed
  account and a framework repo both yield many agents, and adding that later means
  re-rooting the data model rather than extending it.

## Consequences

Enables a second coding agent without re-rooting later, and admits managed and
framework agents where one account or repo holds many. "Which agent is affected"
and "which agent changed" become structural facts of the document rather than
derived by traversal or tag lookup. An agent with no components configured is
reportable, which no place-rooted structure achieves.

Costs. A file that two agents both read is emitted in both BOMs — correct, since
it is genuinely in both compositions and ADR-0038 already permits shared identity
across occurrences, but it is duplication a reader must expect. Emission moves
from one BOM per invocation to many, so the BOM command's single `--output` path
needs a new shape (directory, index, or stream) — an open question in the spec.
Findings stay a single flat list, so `to_sarif`, the one-SARIF-path contract, and
the reference Action's `action.yml` are untouched.

Watch for kind-specific fields appearing on the kind-invariant contract. A
filesystem type there means a control-plane kind will carry dead fields, and the
spec's abstraction-leak check exists to catch it.

Two costs fall on the one kind already implemented. Node-key root labels become
kind-named, which renames every
`bom-ref` once: diffs match on `bom-ref`, so the first scan after upgrade reports
every component removed and re-added and earlier diff history is not comparable
across the boundary. And the hosted side records a single latest BOM per asset,
anchoring every current-state query, so uploading two agent BOMs would let the
second silently displace the first. Uploads therefore keep today's one-BOM
contract, and resolving latest-per-kind gates the *second* kind rather than this
change.

## When to revisit

If components that belong to no agent ever need first-class edges to the agents
they coexist with, the per-place structure becomes the better emission boundary —
noting that direction is the expensive one, since it merges document boundaries
rather than splitting them.

Also revisit if a place emerges whose agents cannot be enumerated at all.
Discovery returning a list is the load-bearing premise; a surface that can only
answer "something is running here" without saying what would need a different
shape.
