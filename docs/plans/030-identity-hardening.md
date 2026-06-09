# Plan 030: Identity Semantics Hardening

## Goal

Centralize ADR-0031 identity semantics so BOM round-trip, OSV federation,
matching, Fleet upload preparation, and Fleet upload-contract validation use
the same component classification rules.

Success criteria:

- `npx`, `uvx`, and `uv tool run` unpinned MCP package launches are classified
  identically across BOM, matcher, OSV federation, Fleet collector, and upload
  contract code.
- `openaca:match_coordinate` is reserved for explicit non-PURL/non-Git external
  audit or registry handles.
- Vulnerability matching consumes typed match coordinates and never falls
  back to graph identity.
- Package-vs-binary MCP classification lives in one helper module.
- Existing imports from `tools.component_ref` keep working during pre-V0
  refactoring.
- Full local gates pass.

## Steps

- [x] Add helper-level matrix tests for identity roles and MCP launch parsing.
- [x] Create a shared identity helper module and re-export existing helper APIs
  from `tools.component_ref`.
- [x] Refactor `tools.bom` to use shared helpers for match coordinate and
  unpinned MCP package inference.
- [x] Refactor `tools.fleet.collector` to use shared helpers for package/binary
  MCP classification and safe unpinned install-source rendering.
- [x] Refactor `tools.fleet.upload_contract` to use the same package/binary MCP
  classification helper as the collector.
- [x] Run focused tests after each refactor and full gates before opening the PR.

## Non-goals

- No rename of `ComponentRef.component_identity`.
- No schema change to overlays.
- No compatibility layer for pre-ADR-0031 BOMs beyond the behavior already
  present in `scan bom`.
