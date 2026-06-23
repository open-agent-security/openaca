# OpenACA Agent BOM Schema

OpenACA Agent BOMs describe agent composition: which agent components were
declared or active, where they came from, and how they relate to each other.
They do not embed vulnerability or posture findings. Findings are separate scan
report data that reference BOM component IDs.

## Format

The external interchange format is CycloneDX JSON. OpenACA emits CycloneDX with
OpenACA-owned metadata in `properties[]` entries whose names start with
`openaca:`.

The initial OpenACA Agent BOM schema version is `0.1`.

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
      {"name": "openaca:schema_version", "value": "0.1"},
      {"name": "openaca:target_type", "value": "repo"}
    ]
  },
  "components": [],
  "dependencies": []
}
```

## Components

Each detected agent component or agent dependency is serialized as one
CycloneDX component.

OpenACA-generated components use their agent graph occurrence identity as the
preferred `bom-ref` when it is unique in the BOM, and carry the same value as
`openaca:identity`. Package-backed components also carry their external package
coordinate as `purl`:

```json
{
  "type": "application",
  "bom-ref": "mcp-server/filesystem",
  "name": "@modelcontextprotocol/server-filesystem",
  "version": "1.0.0",
  "purl": "pkg:npm/%40modelcontextprotocol/server-filesystem@1.0.0",
  "properties": [
    {"name": "openaca:identity", "value": "mcp-server/filesystem"},
    {"name": "openaca:component_type", "value": "mcp_server"},
    {"name": "openaca:scope", "value": "agent-component"},
    {"name": "openaca:source_manifest", "value": ".mcp.json"},
    {"name": "openaca:source_locator", "value": "$.mcpServers.filesystem"}
  ]
}
```

Components use the graph occurrence identity as `openaca:identity`. Package and
Git-backed components use their standard PURL/Git metadata for matching. When a
parser has an explicit non-PURL/non-Git external audit or registry handle,
OpenACA can also emit `openaca:match_coordinate`:

```json
{
  "type": "application",
  "bom-ref": "skill/frontend-design",
  "name": "frontend-design",
  "properties": [
    {"name": "openaca:identity", "value": "skill/frontend-design"},
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
| `openaca:target_type` | `repo`, `endpoint`, or `bom`. Stored on BOM metadata. |
| `openaca:target` | Human-readable target path or endpoint config path when available. |
| `openaca:identity` | OpenACA agent graph occurrence identity. |
| `openaca:match_coordinate` | Explicit external audit or registry coordinate used for matching when no PURL or Git coordinate exists. |
| `openaca:component_type` | Agent component type such as `plugin`, `skill`, `mcp_server`, `hook`, `command`, `agent`, or `package`. |
| `openaca:scope` | Component scope from `ComponentRef.scope`. |
| `openaca:source_manifest` | Manifest or file path where the component was observed. |
| `openaca:source_locator` | Locator inside the source manifest. |
| `openaca:agent_host` | Agent host surface that loads, exposes, or executes the component. |
| `openaca:source_provenance` | JSON-encoded source provenance recovered from lockfiles or symlink targets. |

## Composition Edges

CycloneDX `dependencies[]` stores the composition edges. Each edge runs from a
parent component's `bom-ref` to the `bom-ref` of a component it contains or
declares — a plugin to its bundled skills, MCP servers, hooks, and package
dependencies; a skill to its own bundled deps. This edge set, not a stored
`attributed_to` field, is the source of truth for parentage and attribution
(attribution is the nearest plugin ancestor along these edges).

```json
{
  "ref": "external_plugins/discord/.claude-plugin/plugin.json#$#plugin/discord",
  "dependsOn": ["external_plugins/discord/bun.lock#$.packages['hono']#pkg:npm/hono@4.12.5"]
}
```

## Findings

The Agent BOM intentionally excludes findings. A scan report may contain both a
BOM and findings, but the findings reference BOM components by `bom-ref` rather
than living inside the BOM.
