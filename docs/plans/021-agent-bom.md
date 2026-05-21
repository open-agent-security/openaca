# Agent BOM Implementation Plan

**Goal:** Add an open Agent BOM schema and CLI surface, make scanner flows build
an Agent BOM before matching, and allow stored BOMs to be re-matched with
`openaca scan bom`.

**Architecture:** Reuse `ComponentRef` as parser output, then normalize refs
into an internal `AgentBOM` model. The internal model serializes to CycloneDX
JSON with `openaca:*` properties. Scan commands keep their existing user-facing
behavior but match against BOM components internally.

## Tasks

- [x] Add ADR-0022 documenting Agent BOM as open substrate, CycloneDX
  interchange, BOM-first scan internals, and composition/findings separation.
- [x] Add `docs/openaca-bom-schema.md` with the initial CycloneDX mapping and
  OpenACA property namespace.
- [x] Add tests for BOM component identity, CycloneDX serialization, duplicate
  `bom-ref` disambiguation, and composition edges from `attributed_to`.
- [x] Implement `tools/bom.py` with `AgentBOM`, `BOMComponent`, `BOMEdge`,
  conversion from `ComponentRef`, CycloneDX serialization, and minimal parsing
  from CycloneDX back to `ComponentRef`.
- [x] Add `openaca bom repo` and `openaca bom endpoint` commands that emit a
  composition-only CycloneDX JSON document.
- [x] Refactor `openaca scan repo` and `openaca scan endpoint` to build an
  `AgentBOM` before advisory matching while preserving existing output.
- [x] Add `openaca scan bom --input <file>` to run advisory matching against a
  stored Agent BOM. Posture replay is explicitly out of scope for this command.
- [x] Add focused CLI tests for BOM generation and stored-BOM scanning.
- [x] Run full verification: ruff format/check, pyright, pytest, and
  `openaca lint overlays/`.
