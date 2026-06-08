# Plan 029: Agent Graph Occurrence Identity

## Goal

Make OpenACA-generated BOMs and Fleet upload posture payloads use one
canonical agent-graph occurrence key: `openaca:identity`.

Success criteria:

- Every OpenACA-generated BOM component has `openaca:identity`, including
  package-backed MCP servers and plugin-bundled package dependencies.
- `bom-ref` prefers `openaca:identity`; PURL remains separate source identity.
- Posture findings uploaded to Fleet reference the same identity as the BOM
  component they describe.
- BOM lint rejects PURL-only components that lack `openaca:identity`.
- Fleet upload contract still passes after redaction.

## Steps

- [x] Add failing tests for canonical identity helpers:
  - package-backed MCP server -> `mcp-server/<server-name>` plus package PURL
  - plugin-bundled package dep -> `<plugin>/deps/<ecosystem>/<package-name>`
  - explicit source-less identities still round-trip
- [x] Add failing BOM tests:
  - generated package-backed components include `openaca:identity`
  - generated `bom-ref` prefers `openaca:identity` over PURL
  - linter rejects PURL-only components without `openaca:identity`
- [x] Add failing Fleet collector tests:
  - remote MCP insecure-transport posture identity matches BOM identity
  - unpinned/package-backed MCP mutable-install posture identity matches BOM identity
- [x] Implement canonical identity helpers in `tools.component_ref`.
- [x] Update BOM serialization to emit canonical `openaca:identity`, use it for
  `bom-ref`, and build attribution edges against canonical identities.
- [x] Update posture rules and Fleet upload payload conversion to carry canonical
  identities directly, leaving alignment as defensive compatibility.
- [x] Update BOM lint text and validation to require `openaca:identity`.
- [x] Run focused tests, then the relevant OpenACA test suite.

## Non-goals

- No schema change to OpenACA overlays.
- No new advisory identity namespace.
- No migration for pre-V0 BOMs already generated with `mcp-remote/*` identities.
