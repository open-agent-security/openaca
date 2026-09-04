# Identity Model

OpenACA exposes two component identifiers and keeps advisory matching separate.

## Occurrence: `bom-ref`

Every graph node has a `bom-ref` that is unique inside one Agent BOM. Graph
edges, findings, posture, and observations attach to this exact occurrence.
Containment belongs in these edges rather than in a second occurrence ID.

## The agent: `(asset, kind, agent id)`

A BOM's subject is one agent, and identifying it takes two layers, because the
document is deliberately de-identified (ADR-0045):

| Part | Where it comes from |
|---|---|
| asset | wherever the document was collected or read from |
| `openaca:agent_kind` | the document |
| `openaca:agent_id` | the document, **when present** — absent for a kind with one agent per place, where asset plus kind already resolve |

Two documents agreeing on all three describe the same agent at two points in
time and are comparable. Differing in any one, they are different agents. Nothing
else is part of the key: not `openaca:composition_source`, not the
`openaca:target` path, not the content hash.

The agent's `bom-ref` is prefixed `root/` — **not** `agent/`, which the closed
component-type set already uses for a subagent the runtime loads
(`openaca:identity: agent/reviewer`). The two never collide: the subject is
`metadata.component` carrying `openaca:agent_kind`, while a subagent is a
`components[]` row carrying `openaca:component_type: agent`.

An agent's kind is **not** its identity. `openaca:identity` means one specific
logical component; a runtime kind is a category, and two agents of one kind are
two different agents rather than two occurrences of one. `openaca:identity` is
unchanged by the agent root.

Two workstations with identical configuration are separate instances whose
compositions are *comparable*: the path normaliser strips machine-specific roots,
so the same config yields byte-identical `bom-ref`s. Comparable is not identical
— node-key equality covers the components a BOM carries, and says nothing about
settings that are not components (a model selection, permission rules,
environment, system instructions).

## Cross-BOM identity: `openaca:identity`

`openaca:identity` is an optional source-stable join key for the same logical
component across BOMs. It is role-qualified and version-independent:

```text
mcp-server/npm/@modelcontextprotocol/server-filesystem
package/npm/@modelcontextprotocol/server-filesystem
plugin/claude-plugins-official/discord
package/npm/lodash
mcp-remote/api.example.com/mcp
```

Local aliases and display names are not identities. When no trustworthy source
namespace exists, `openaca:identity` is absent and the component remains
occurrence-local. A plugin namespaces a private child only when that child has
no independent source, for example
`skill/plugin/claude-plugins-official/discord/configure`.

Cross-BOM consumers group only non-null identities. Versions stay on the
observed occurrence rather than splitting logical identity.

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

A single external package can appear multiple times in an agent stack and can
also play multiple roles. The same source package may therefore have both a
`package/npm/...` identity and an `mcp-server/npm/...` identity.

Two occurrences of the same role and source share `openaca:identity`, but remain
distinct graph nodes with distinct `bom-ref`s and parent edges. Attribution is
preserved by those occurrence nodes and `dependencies[]`, not by identity.

Conversely, many graph components do not have package coordinates at all:
local skills, source-less hooks, local commands, and direct binary launches can
still be inventoried and evaluated for posture, but they cannot be matched to
version-specific package advisories until a match coordinate exists.

## Agent BOM usage

Agent BOMs always carry occurrence `bom-ref`s and carry `openaca:identity` only
when it can be derived from stable source facts. Match coordinates remain
separate when a component can be queried against OSV.dev or another source.

This lets a BOM answer both questions:

- what is installed or declared in this agent stack?
- which external package, Git source, or audit source should be used for
  advisory matching?
