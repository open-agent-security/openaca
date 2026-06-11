# Identity Model

OpenACA separates where a component appears in an agent graph from the external
coordinate used to match it against advisory sources.

## Graph identity

`openaca:identity` identifies an occurrence in the agent composition graph:

```text
host -> plugin -> skill / mcp-server / hook -> dependency
```

Graph identities answer questions such as:

- where did this component enter the agent stack?
- which plugin, skill, MCP server, hook, or command introduced it?
- what should the UI show in an Agent BOM or finding path?

Graph identities are not advisory match keys. They describe local agent
composition, not external package or source coordinates.

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

Those occurrences should share a match coordinate when they refer to the same
external package version, but they should not share a graph identity. The graph
identity preserves attribution: which stack path introduced the finding.

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
