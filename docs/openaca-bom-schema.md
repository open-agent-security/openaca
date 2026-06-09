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

Each detected agent component is serialized as one CycloneDX component.

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
| `openaca:component_type` | Agent component type such as `plugin`, `skill`, `mcp_server`, `hook`, `command`, `agent`, or `component`. |
| `openaca:scope` | Component scope from `ComponentRef.scope`. |
| `openaca:source_manifest` | Manifest or file path where the component was observed. |
| `openaca:source_locator` | Locator inside the source manifest. |
| `openaca:attributed_to` | Parent plugin identity when a component was discovered through an active plugin. |
| `openaca:agent_host` | Agent host surface that loads, exposes, or executes the component. |
| `openaca:source_provenance` | JSON-encoded source provenance recovered from lockfiles or symlink targets. |

## Composition Edges

CycloneDX `dependencies[]` stores composition edges. When a component has
`attributed_to` set to a plugin identity present in the BOM, OpenACA emits a
dependency edge from the plugin's `bom-ref` to the child component's `bom-ref`.

```json
{
  "ref": "plugin/claude-plugins-official/github@unknown",
  "dependsOn": ["mcp-remote/api.githubcopilot.com/mcp/"]
}
```

## Findings

The Agent BOM intentionally excludes findings. A scan report may contain both a
BOM and findings, but the findings reference BOM components by `bom-ref` rather
than living inside the BOM.
