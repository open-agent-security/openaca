---
id: 0042
title: Use bom-ref for occurrences and optional source-stable identity for cross-BOM joins
status: accepted
date: 2026-07-09
supersedes: [0029, 0030]
superseded-by: null
---

## Context

The scanner currently exposes several identifiers whose names and behavior
overlap. `bom-ref` identifies a graph node, `openaca:identity` is documented as
a cross-BOM join key, source coordinates identify upstream software, and local
configuration aliases sometimes become `openaca:identity`. That last behavior
breaks the cross-BOM promise: two aliases can split one package-backed MCP
server, while one reused alias can merge different servers.

Containment added another tempting source of identity. A package, MCP server,
or skill can sit inside a plugin, but including the parent in every child
identity would make independently sourced software change identity whenever it
is installed in a different container. Conversely, a plugin-private child with
no independent source does need the plugin namespace to avoid colliding with an
unrelated private child of the same name.

The result should be explainable as two identifiers, not a growing hierarchy
of component, subject, graph, and rollup identities.

## Decision

OpenACA has two component identifiers:

1. **`bom-ref` identifies an exact occurrence inside one Agent BOM.** Graph
   edges and occurrence-level findings, posture, and observations join on it.
   It is unique within the BOM and may include manifest and locator context.
2. **`openaca:identity` optionally identifies the same sourced component
   across BOMs.** It is version-independent, role-qualified, and emitted only
   when the scanner has a stable source namespace. Inventory, drift,
   capabilities, policy, and fleet aggregation may group on it.

An absent `openaca:identity` is meaningful. A component with no trustworthy
source namespace remains occurrence-local and must not be merged across BOMs.
Local aliases and display names never become cross-BOM identity.

Identity is role-qualified because the same code can play different roles:

```text
mcp-server/npm/@modelcontextprotocol/server-filesystem
package/npm/@modelcontextprotocol/server-filesystem
plugin/claude-plugins-official/discord
package/npm/hono
mcp-remote/api.example.com/mcp
```

Versions do not appear in `openaca:identity`; observed versions remain source
metadata. Matching also remains separate: PURL, Git, package, and explicit
external audit coordinates are typed source facts and vulnerability matching
never falls back to `openaca:identity`.

Containment is represented by `bom-ref` edges and composition paths. A parent
plugin is included in a child's identity only when the plugin is the
authoritative namespace for a private child with no independent source, for
example:

```text
skill/plugin/claude-plugins-official/discord/configure
mcp-server/plugin/claude-plugins-official/discord/reply
```

An independently sourced MCP server, skill, or package keeps the same identity
regardless of which plugin contains or launches it.

Identity construction and match-coordinate construction remain centralized in
shared helper APIs. The Agent BOM schema advances from 0.3 to 0.4 because
`openaca:identity` becomes optional and its semantics change. Pre-V1 consumers
must update; no compatibility alias is emitted.

ADR-0037's graph decision remains accepted, with `node.key`/`bom-ref` as its
occurrence key. This ADR replaces the alias-shaped identity decision introduced
by ADR-0029 and the helper contract in ADR-0030. ADR-0031's matching boundary
and ADR-0038's occurrence-key-versus-join-key distinction remain accepted; this
ADR makes the join key optional and source-stable.

## Alternatives considered

- **Keep alias-based identities and add a fleet rollup key.** Rejected because
  it creates another identity system and leaves local aliases load-bearing.
- **Put the full containment path in every identity.** Rejected because the
  same independently sourced component would change identity under each parent;
  the graph already records containment.
- **Use source coordinates directly as the only identifier.** Rejected because
  one source artifact can play multiple roles, and many local occurrences have
  no stable source coordinate.
- **Require identity for every component.** Rejected because a fabricated
  stable key is worse than explicit unknown coverage; unknown components remain
  useful through `bom-ref`.
- **Keep versions in identity.** Rejected because upgrades would split the same
  logical component across time and hide fleet blast radius.

## Consequences

Cross-BOM joins become one rule: group by non-null `openaca:identity`; otherwise
keep the occurrence local. Within a BOM, every join uses `bom-ref`. Consumers no
longer need to infer whether a label, PURL, or old component ID is safe to merge.

The reset changes existing MCP and skill identities, makes the BOM identity
property optional, moves occurrence-level signal attachment to `bom-ref`, and
requires downstream ingestion updates. Capability descriptors can simplify to
identity-keyed lookup once package-backed MCP identities are source-derived.

The cost is a pre-V1 schema migration and a partial rework of recently added
identity-keyed capability data. The benefit is removing ambiguity before
cross-asset exposure rollups make incorrect joins user-visible.

## When to revisit

Revisit if a standard component identifier can express both role and source
without losing occurrence semantics, or if a future source type cannot be
represented without adding a third identifier. Do not revisit merely to make
unknown local components aggregatable; that would recreate alias-based joins.
