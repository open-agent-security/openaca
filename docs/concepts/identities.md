# Identity Model

OpenACA separates where a component appears in an agent graph from the external
coordinate used to match it against advisory sources.

## Graph identity

`openaca:identity` is a component's **canonical identity** within the agent
composition graph — the type-prefixed name (`plugin/<name>`, `mcp-server/<name>`,
`package/<ecosystem>/<name>`) that is **shared across every occurrence** of that
component. The agent stack is a containment graph:

```text
host -> plugin -> skill / mcp-server / hook -> dependency
```

Each *occurrence* of a component is a distinct graph node keyed by its `bom-ref`
(not `openaca:identity`), and the graph **edges** record which plugin or skill
contains it. So `openaca:identity` together with the edges answer:

- where did this component enter the agent stack? (its node's lineage)
- which plugin, skill, MCP server, hook, or command introduced it? (its nearest
  plugin/skill ancestor along the edges)
- what should the UI show in an Agent BOM or finding path?

`openaca:identity` is the cross-occurrence join key (posture, drift, policy,
Fleet); the per-occurrence key is `bom-ref`. See ADR-0038. Neither is an advisory
match key — they describe local agent composition, not external package or source
coordinates.

## Match coordinate

Match coordinates identify the external thing that can be queried against an
advisory or audit source.

Examples:

- `pkg:npm/@modelcontextprotocol/server-filesystem@1.0.0`
- `pkg:pypi/example-mcp@2.3.0`
- a Git repository coordinate with a commit or tag when the advisory source
  supports Git matching
- an explicit external audit coordinate when a component ecosystem has its own
  advisory source

Match coordinates answer questions such as:

- can OSV.dev match this package and version?
- can another advisory source match this Git or registry coordinate?
- is the component versioned enough to evaluate a known advisory?

## Why they are separate

A single external package can appear multiple times in an agent stack. For
example, one vulnerable npm package might be bundled by a plugin and also
launched directly as an MCP server.

Those occurrences share both a match coordinate (same external package version)
and a graph identity (`openaca:identity`, e.g. `package/npm/lodash`) — but they
are **distinct graph nodes** with distinct `bom-ref`s and distinct parent edges.
Attribution — which stack path introduced a given finding — is preserved by
those per-occurrence `bom-ref` nodes and the `dependencies[]` edges (the nearest
plugin/skill ancestor), not by `openaca:identity`.

Conversely, many graph components do not have package coordinates at all:
local skills, source-less hooks, local commands, and direct binary launches can
still be inventoried and evaluated for posture, but they cannot be matched to
version-specific package advisories until a match coordinate exists.

## Agent BOM usage

Agent BOMs carry graph identity for composition and attribution. They carry
match coordinates separately when a component can be matched against OSV.dev or
another advisory source.

This lets a BOM answer both questions:

- what is installed or declared in this agent stack?
- which external package, Git source, or audit source should be used for
  advisory matching?
