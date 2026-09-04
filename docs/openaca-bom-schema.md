# OpenACA Agent BOM Schema

OpenACA Agent BOMs describe agent composition: which agent components were
declared or active, where they came from, and how they relate to each other.
They do not embed vulnerability or posture findings. Findings are separate scan
report data that reference BOM component IDs.

## Format

The external interchange format is CycloneDX JSON. OpenACA emits CycloneDX with
OpenACA-owned metadata in `properties[]` entries whose names start with
`openaca:`.

The current OpenACA Agent BOM schema version is `0.5`. It roots the document on
the agent rather than the place it was scanned from (ADR-0044): `metadata.component`
is the agent, and `bom-ref` is the exact occurrence key (ADR-0042). `openaca bom
lint` accepts `0.1` through `0.5`; the emitter produces `0.5` for every
agent-rooted document. The one exception is a graphless call, which produces no
`metadata.component` at all, emits the pre-`0.5` shape and stamps it `0.4` to
match.

The machine-readable OpenACA profile lives at
`schema/openaca-bom.schema.json`. Validate a BOM with:

```bash
openaca bom lint agent.bom.json
```

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.7",
  "version": 1,
  "metadata": {
    "tools": [
      {
        "vendor": "OpenACA",
        "name": "openaca"
      }
    ],
    "properties": [
      {"name": "openaca:schema_version", "value": "0.5"}
    ],
    "component": {
      "type": "application",
      "bom-ref": "root/claude-code",
      "name": "Claude Code",
      "properties": [
        {"name": "openaca:agent_kind", "value": "claude-code"},
        {"name": "openaca:composition_source", "value": "installed"},
        {"name": "openaca:composition_coverage", "value": "complete"}
      ]
    }
  },
  "components": [],
  "dependencies": [{"ref": "root/claude-code", "dependsOn": []}]
}
```

## The document's subject is one agent

At `0.5` a BOM describes one **agent** — one runtime plus the composed context it
loads — not one place (ADR-0044). `metadata.component` *is* that agent: its
`bom-ref` is `root/<kind>` (or `root/<kind>/<agent_id>` for a kind that can have
more than one agent in one place), and its `name` is the agent's human-readable
label. A scan emits one document per agent it discovers, which is one document
today because Claude Code is the only registered kind.

`root/` is deliberately not `agent/`: the closed component-type set already uses
`agent/<name>` for a **subagent** the runtime loads, and the two must not share a
namespace (ADR-0045).

An agent's instance key spans two layers — the asset (which place) comes from
wherever the document was collected or read from, and `openaca:agent_kind` plus
`openaca:agent_id` (which sort of agent) come from the document. A document deliberately carries no place identity.

Node-key root labels name the kind that **owns** the config root a path came from
(`claude-code/<rel>`), so a file one runtime compat-reads from another's config
root carries the same key in both agents' documents — it is one file. `project/<rel>`
is unchanged, and a repo scan's keys stay bare relative paths.

## Components

Each detected agent component or agent dependency is serialized as one
CycloneDX component.

Every component has a `bom-ref`, the exact occurrence key used by graph edges
and occurrence-level findings. A component also has `openaca:identity` when the
scanner can derive a trustworthy, version-independent source namespace. The
identity is role-qualified, for example
`mcp-server/npm/@modelcontextprotocol/server-filesystem`,
`package/npm/@modelcontextprotocol/server-filesystem`, or
`plugin/claude-plugins-official/discord`. The same source can have distinct
identities in distinct roles.

Local aliases are display data, not identity. A direct local skill, hook, or
binary MCP without a trustworthy source omits `openaca:identity` and remains
occurrence-local. A plugin-private child may use its source-stable plugin as an
authoritative namespace, such as
`skill/plugin/claude-plugins-official/discord/configure`. Independently sourced
children never include their containing plugin in identity; containment stays
in `dependencies[]`.

**Which key to join on.** For joins *within* a BOM — following `dependencies[]`
edges, or resolving a finding's `bom-ref` to its component — use `bom-ref` (the
occurrence/node key). For grouping the *same logical component across*
occurrences, scans, or time (posture, drift, policy, Fleet rows) — group only on
non-null `openaca:identity`. A missing identity means "do not merge across
BOMs." Do not join occurrence-level rows on `openaca:identity`.

Package-backed components also carry their external package coordinate as `purl`:

```json
{
  "type": "application",
  "bom-ref": ".mcp.json#$.mcpServers.filesystem#mcp-server/npm/@modelcontextprotocol/server-filesystem",
  "name": "@modelcontextprotocol/server-filesystem",
  "version": "1.0.0",
  "purl": "pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.0",
  "properties": [
    {"name": "openaca:identity", "value": "mcp-server/npm/@modelcontextprotocol/server-filesystem"},
    {"name": "openaca:component_type", "value": "mcp_server"},
    {"name": "openaca:scope", "value": "agent-component"},
    {"name": "openaca:source_manifest", "value": ".mcp.json"},
    {"name": "openaca:source_locator", "value": "$.mcpServers.filesystem"}
  ]
}
```

Package and Git-backed components use their standard PURL/Git metadata for
matching. Vulnerability matching never falls back to `openaca:identity`. When a
parser has an explicit non-PURL/non-Git external audit or registry handle,
OpenACA can also emit `openaca:match_coordinate`:

```json
{
  "type": "application",
  "bom-ref": "skill/skills.sh/anthropics/skills/frontend-design",
  "name": "frontend-design",
  "properties": [
    {"name": "openaca:identity", "value": "skill/skills.sh/anthropics/skills/frontend-design"},
    {"name": "openaca:match_coordinate", "value": "skills.sh:anthropics/skills/frontend-design"},
    {"name": "openaca:component_type", "value": "skill"}
  ]
}
```

Plugin-bundled package dependencies use CycloneDX `type: "library"` and
`openaca:component_type: "package"`. The package identity is **not**
parent-qualified — its relationship to the parent plugin is expressed by a
`dependencies[]` edge (see Composition Edges), while the per-occurrence
`bom-ref` keys this specific appearance under the plugin's lockfile:

```json
{
  "type": "library",
  "bom-ref": "external_plugins/discord/bun.lock#$.packages['hono']#pkg:npm/hono@4.12.5",
  "name": "hono",
  "version": "4.12.5",
  "purl": "pkg:npm/hono@4.12.5",
  "properties": [
    {"name": "openaca:identity", "value": "package/npm/hono"},
    {"name": "openaca:component_type", "value": "package"},
    {"name": "openaca:scope", "value": "agent-dependency"},
    {"name": "openaca:source_manifest", "value": "external_plugins/discord/bun.lock"},
    {"name": "openaca:source_locator", "value": "$.packages['hono']"}
  ]
}
```

If the preferred `bom-ref` is duplicated, OpenACA appends a stable short hash
suffix derived from the component observation fields.

## OpenACA Properties

| Property | Meaning |
|---|---|
| `openaca:schema_version` | OpenACA Agent BOM schema version. Stored on BOM metadata. |
| `openaca:agent_kind` | What reads this composition, e.g. `claude-code`. Stored on `metadata.component`; required on an agent-rooted document. |
| `openaca:composition_source` | `installed` (read from a place where the agent is provisioned) or `declared` (read from a repo declaration). Stored on `metadata.component`; **required and explicit** — declared results stay out of exposure counts, so a missing value would turn potential exposure into actual. |
| `openaca:composition_coverage` | How much of this agent's composition the scan could observe: `unknown`, `partial`, or `complete` (ADR-0046). Resolved per composition source as `min(baseline, evidence)`, so a manifest that failed to parse downgrades it. Distinct from the per-component `openaca:capability_coverage`. |
| `openaca:agent_id` | The identifier the kind's own surface uses to address one agent, for kinds that can have more than one agent in one place. **Absent** for a singleton kind such as Claude Code. Part of the instance key, never a renameable label. |
| `openaca:target` | Where this agent's composition was read from — a path when the caller asked for one, **absent** when it asked for a document that names no place. Distinct from `openaca:composition_source`, which records whether that was a running agent or a declaration. |
| `openaca:identity` | Optional source-stable, version-independent, role-qualified cross-BOM join key (ADR-0042). Missing means the component must remain occurrence-local. |
| `openaca:match_coordinate` | Explicit external audit or registry coordinate used for matching when no PURL or Git coordinate exists. |
| `openaca:component_type` | Agent component type such as `plugin`, `skill`, `mcp_server`, `hook`, `command`, `agent`, or `package`. |
| `openaca:scope` | Component scope from `ComponentRef.scope`. |
| `openaca:source_manifest` | Manifest or file path where the component was observed. |
| `openaca:source_locator` | Locator inside the source manifest. |
| `openaca:source_provenance` | JSON-encoded source provenance recovered from lockfiles or symlink targets. |
| `openaca:capabilities` | JSON-encoded list of capability descriptors (closed taxonomy). Component descriptor, not a finding (ADR-0041). |
| `openaca:capability_coverage` | Whether a capability-reading mechanism applied to this component: `unknown` (none did), `partial`, or `complete`. Derived from mechanism applicability, **never** from whether the resulting capability list is empty — a covered component that declares none of the taxonomy is `partial` with an empty list, which is a real answer and not the same claim as `unknown` (ADR-0041 principle 2). Component descriptor, not a finding. |

### Read for stored documents, no longer written

These are still restored when reading a stored `0.4` document, and are never
emitted at `0.5` — removing a property means stopping the write, not the read.

| Property | Replaced by |
|---|---|
| `openaca:target_type` | The `metadata.component` `bom-ref` prefix answers "what is this document about"; `openaca:composition_source` answers "how was it produced". |
| `openaca:agent_host` | The document's subject carries the runtime. |
| `openaca:runtime_hosts` | The same. A component's "active in" comes from the agent that scanned it, not from the parser that read the file. |

## Composition Edges

CycloneDX `dependencies[]` stores the composition edges. Each edge runs from a
parent component's `bom-ref` to the `bom-ref` of a component it contains or
declares — a plugin to its bundled skills, MCP servers, hooks, and package
dependencies; a skill to its own bundled deps. This edge set, not a stored
`attributed_to` field, is the source of truth for parentage and attribution
(attribution is the nearest plugin ancestor along these edges).

```json
{
  "ref": "claude-code/plugins/installed_plugins.json#$.plugins.discord#plugin/claude-plugins-official/discord",
  "dependsOn": ["external_plugins/discord/bun.lock#$.packages['hono']#pkg:npm/hono@4.12.5"]
}
```

## Findings

The Agent BOM intentionally excludes findings. A scan report may contain both a
BOM and findings, but the findings reference BOM components by `bom-ref` rather
than living inside the BOM.
