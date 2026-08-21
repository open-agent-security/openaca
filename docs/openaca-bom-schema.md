# OpenACA Agent BOM Schema

OpenACA Agent BOMs describe agent composition: which agent components were
declared or active, where they came from, and how they relate to each other.
They do not embed vulnerability or posture findings. Findings are separate scan
report data that reference BOM component IDs.

## Format

The external interchange format is CycloneDX JSON. OpenACA emits CycloneDX with
OpenACA-owned metadata in `properties[]` entries whose names start with
`openaca:`.

The current OpenACA Agent BOM schema version is `0.5`. It makes
`openaca:identity` optional and source-stable while retaining `bom-ref` as the
exact occurrence key (ADR-0042). `openaca bom lint` accepts `0.1`, `0.2`,
`0.3`, `0.4`, and `0.5`; the emitter always produces `0.5`.

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
      {"name": "openaca:schema_version", "value": "0.5"},
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
| `openaca:target_type` | `repo`, `endpoint`, or `bom`. Stored on BOM metadata. |
| `openaca:target` | Human-readable target path or endpoint config path when available. Single-host endpoint BOMs (and repo BOMs) carry the selected host's config root — the documented API-compatibility anchor. Multi-host endpoint BOMs carry the neutral locator `endpoint:user-scope` instead, since no single host's root is authoritative; per-host roots are still available via `openaca:host_config_roots`. |
| `openaca:source_unit_count` | Count of the unit named by `openaca:source_unit_label` (e.g. manifests parsed, plugins found). Stored on BOM metadata. |
| `openaca:source_unit_label` | Unit name for `openaca:source_unit_count`: `manifest`, `active plugin`, or (endpoint BOMs where any selected host's plugins are presence-only, e.g. Cursor per ADR-0045 Decision #7) `plugin` — "active" is never asserted for a host that cannot observe enabled state. |
| `openaca:scanned_hosts` | JSON-encoded array of the host ids the scan covered, in root-map order (endpoint) or host-registry order (repo, which always walks every registered host). Stored on **every** BOM this version writes, single-host included: it is the only record of which hosts were looked at, and a reader cannot recover it from components — an endpoint that turned up nothing has no per-component attribution to infer from. Absence therefore means the BOM predates host awareness, and a reader should treat it as `claude-code`. |
| `openaca:host_config_roots` | JSON-encoded object mapping each selected host id to its config root path. Stored **only when 2+ hosts are selected**: a single-host selection has exactly one authoritative root, already carried by `openaca:target`. |
| `openaca:identity` | Optional source-stable, version-independent, role-qualified cross-BOM join key (ADR-0042). Missing means the component must remain occurrence-local. |
| `openaca:match_coordinate` | Explicit external audit or registry coordinate used for matching when no PURL or Git coordinate exists. |
| `openaca:component_type` | Agent component type such as `plugin`, `skill`, `mcp_server`, `hook`, `command`, `agent`, or `package`. |
| `openaca:scope` | Component scope from `ComponentRef.scope`. |
| `openaca:source_manifest` | Manifest or file path where the component was observed. |
| `openaca:source_locator` | Locator inside the source manifest. |
| `openaca:source_provenance` | JSON-encoded source provenance recovered from lockfiles or symlink targets. |
| `openaca:capabilities` | JSON-encoded list of capability descriptors (closed taxonomy). Component descriptor, not a finding (ADR-0041). |
| `openaca:capability_coverage` | Capability extraction coverage: `unknown`, `partial`, or `complete`. Component descriptor, not a finding (ADR-0041). |

## Composition Edges

CycloneDX `dependencies[]` stores the composition edges. Each edge runs from a
parent component's `bom-ref` to the `bom-ref` of a component it contains or
declares — a plugin to its bundled skills, MCP servers, hooks, and package
dependencies; a skill to its own bundled deps. This edge set, not a stored
`attributed_to` field, is the source of truth for parentage and attribution
(attribution is the nearest plugin ancestor along these edges).

```json
{
  "ref": "endpoint/plugins/installed_plugins.json#$.plugins.discord#plugin/claude-plugins-official/discord",
  "dependsOn": ["external_plugins/discord/bun.lock#$.packages['hono']#pkg:npm/hono@4.12.5"]
}
```

## Findings

The Agent BOM intentionally excludes findings. A scan report may contain both a
BOM and findings, but the findings reference BOM components by `bom-ref` rather
than living inside the BOM.
