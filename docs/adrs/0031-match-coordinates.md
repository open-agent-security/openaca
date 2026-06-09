---
id: 0031
title: Separate graph identity from match coordinates
status: accepted
date: 2026-06-08
supersedes: [0019, 0029]
superseded-by: null
---

## Context

ADR-0029 made `openaca:identity` the agent graph occurrence key. That was the
right direction for inventory, posture, drift, policy, and Fleet joins, but it
left one confusing edge: the document still described
`openaca:source_identity` as a matching identity for source-less components.

That phrasing made graph identity and advisory matching feel like peer identity
systems. They are not. `openaca:identity` answers "where does this component
occur in the agent graph?" Matching answers "what package, Git object, or
external audit handle can a signal source target?"

## Decision

`openaca:identity` remains the scanner-owned graph occurrence key. It is
required on generated Agent BOM components and is the join key for posture,
drift, policy, inventory, and Fleet rows.

ADR-0019's separation between source ecosystems and agent component types still
holds: `ecosystem` is reserved for package/source naming spaces such as npm,
PyPI, GitHub, and Docker. Agent component families such as plugins, skills,
hooks, commands, agents, and MCP servers remain component types and graph
occurrences, not package ecosystems.

Vulnerability matching never falls back to `openaca:identity`. Matching uses
derived match coordinates:

- standard package PURLs for versioned npm/PyPI package components;
- OSV Git commit or Git version coordinates for supported Git refs;
- unpinned package coordinates derived from MCP launch provenance such as
  `npx`, `uvx`, and `uv tool run`;
- `openaca:match_coordinate` for explicit non-PURL/non-Git external audit or
  registry handles.

The BOM field formerly called `openaca:source_identity` is renamed to
`openaca:match_coordinate`. It is emitted only when a parser has a concrete
external handle that is matchable but not representable as PURL or Git. It is
not a second graph identity and is not inferred from local component names,
remote URLs, or old native identity strings.

Internally, consumers should use a typed match-coordinate helper rather than
reconstructing matching semantics from raw `openaca:*` strings.

## Examples

Package-backed MCP:

```text
openaca:identity = mcp-server/playwright
purl             = pkg:npm/%40playwright/mcp
match coord      = package npm @playwright/mcp
```

Remote MCP:

```text
openaca:identity = mcp-server/asana
match coord      = none
posture data     = transport/url/install context
```

Skill with external audit provenance:

```text
openaca:identity         = skill/frontend-design
openaca:match_coordinate = skills.sh:anthropics/skills/frontend-design
match coord              = external audit skills.sh:anthropics/skills/frontend-design
```

Direct local skill with no match coordinate:

```text
openaca:identity = skill/local-helper
match coord      = none
```

The remote MCP and direct local skill are still inventory, posture, drift, and
policy data. They are not vulnerability-matchable unless a parser recovers a
real package, Git, or external audit coordinate.

## Alternatives considered

- **Continue matching source-less components by graph identity.** Rejected
  because graph identity is local to the observed agent stack. Treating it as an
  advisory key makes local names look like upstream source coordinates.
- **Store a single `openaca:match_coordinate` field in the BOM.** Rejected
  because PURL and Git coordinates already have standard locations and because
  query shapes differ by advisory backend. The BOM stores source facts; the
  matcher derives query coordinates.
- **Keep the `source_identity` name.** Rejected because the word "identity"
  repeats the ambiguity that caused this clarification. `match_coordinate`
  describes an external signal join key without implying graph identity.

## Consequences

Graph-only components no longer produce vulnerability findings from
`database_specific.openaca.component_identity`. Existing V0 posture and
inventory use cases keep working because they already join on `openaca:identity`.

Non-PURL/non-Git match-coordinate records must target
`database_specific.openaca.match_coordinate`. Package-backed advisories should
continue to use upstream package/Git data.

Pre-release consumers must update from `openaca:source_identity` to
`openaca:match_coordinate`; no compatibility alias is maintained.
