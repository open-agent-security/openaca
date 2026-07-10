---
id: 0043
title: Share one exposure decision contract across local and fleet consumers
status: accepted
date: 2026-07-09
supersedes: null
superseded-by: null
---

## Context

ADR-0040 separates scan evidence from triage decisions, but the first card
shape exposes a `component_id` assembled from versioned plugin labels, PURLs,
match coordinates, identities, or display-name fallbacks. That key is useful
inside one report but unsafe for cross-BOM aggregation and introduces a third
identity concept beside `bom-ref` and `openaca:identity`.

The first scan contract also represents composition paths as type/name pairs.
That is enough to render a local tree, but a downstream consumer cannot resolve
an ancestor path node to its exact BOM occurrence or group it safely across
BOMs. Reimplementing grouping and decision rules downstream would let local
and fleet exposure cards drift again.

## Decision

OpenACA owns one exposure engine and one card contract. Downstream consumers
use it through the curated `openaca.core` facade established by ADR-0028.

An exposure card carries:

- `component`: optional source-stable `identity`, `type`, display `name`, and
  observed `versions`;
- `occurrences`: exact `bom_ref` values, each with one or more composition paths
  and active host facts;
- evidence, priority, confidence, recommended action, explanation, and scope
  limits computed by the shared engine.

There is no public `component_id`, rollup ID, or label-derived fallback
identity. Inside one scan, the engine groups by non-null `openaca:identity` and
otherwise by `bom-ref`. This is the ADR-0042 join rule applied directly to the
decision layer.

Every component path node carries `type`, display `name`, exact `bom_ref`,
nullable `identity`, and observed `version` when known. Finding evidence also
carries the `bom_ref` of the occurrence that produced it. One selected
component occurrence may retain several composition paths so grouping does not
discard child-specific lineage.

Component-scoped evidence without an occurrence key is invalid input. Asset-
scoped posture remains valid scan evidence but does not become a component
exposure card.

Fleet consumers may add cross-asset counts, asset lists, first/last seen, and
freshness. Because `bom-ref` is unique only within one BOM, they retain asset
scope outside the local card rather than namespace or globally merge occurrence
keys. They feed merged evidence and composition paths through the shared
`decide_exposure` function for priority, action, and `why_it_matters`; they do
not copy a representative card's decision fields or maintain parallel rules.

## Alternatives considered

- **Add a separate fleet rollup identity.** Rejected because ADR-0042 already
  defines the only safe cross-BOM join key and explicit behavior for unknowns.
- **Keep `component_id` but tighten its meaning.** Rejected because a per-scan
  occurrence key and a cross-BOM identity have different semantics; one field
  would hide that distinction again.
- **Let each consumer aggregate local cards.** Rejected because merging already
  interpreted cards cannot reliably recompute action and explanation from the
  full evidence set.
- **Put fleet fields in the OSS card.** Rejected because asset counts and
  first/last seen do not exist in a single scan and do not belong in the local
  domain model.

## Consequences

The structured scan artifact becomes a stronger evidence contract: paths are
machine-resolvable, and posture, observation, and vulnerability findings all
point to exact occurrences. Local CLI reports change JSON shape before V1.

Consumers gain one supported decision seam and can render the same vocabulary
without importing `tools.*`. Fleet aggregation remains a separate concern, but
its decisions are reproducible from the shared engine.

## When to revisit

Revisit if an exposure must span several logical components rather than select
one component subject, or if asset-scoped policy evidence needs its own decision
object. Do not add another component identifier to solve either problem.
