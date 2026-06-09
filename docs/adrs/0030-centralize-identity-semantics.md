---
id: 0030
title: Centralize identity semantics behind helper APIs
status: accepted
date: 2026-06-08
supersedes: null
superseded-by: null
---

## Context

ADR-0031 makes `openaca:identity` the agent graph occurrence key and routes
matching through derived match coordinates from PURL, Git metadata, package
launch provenance, and `openaca:match_coordinate`. The model is sound, but the
implementation still spread identity semantics
across `tools/component_ref.py`, `tools/bom.py`, `tools/matcher.py`,
`tools/osv_federation.py`, `tools/fleet/collector.py`, and
`tools/fleet/upload_contract.py`.

That made review feedback repetitive. Each fix repaired one sink while another
sink still inferred "what kind of thing is this?" from its own combination of
string prefixes, PURLs, `install_source`, and MCP launcher tokens. The most
visible example was unpinned MCP package launch handling: `npx`, `uvx`, and
`uv tool run` needed identical semantics for BOM round-trip, OSV queries,
matching, Fleet upload trimming, and upload-contract validation.

## Decision

Identity and match-coordinate semantics live behind shared helper APIs. Parser
outputs still use `ComponentRef`, generated BOMs emit ADR-0031 fields, and
consumers must call the shared helpers for:

- canonical graph occurrence identity;
- match coordinate derivation;
- unpinned MCP package launch recognition;
- package extraction from MCP launcher commands;
- Fleet-safe unpinned package install-source rendering;
- package-vs-binary MCP classification used by upload preparation and upload
  contract validation.

Callers may still own presentation, serialization, or transport-specific
concerns, but they must not independently reimplement identity classification
from raw string prefixes or first-token launcher checks.

## Alternatives considered

- **Leave the logic where it is and add more sink-specific tests.** Rejected
  because the same bug class already recurred across several sinks. More tests
  help, but duplicated logic still invites divergent behavior.
- **Rename `ComponentRef.component_identity` into separate fields now.**
  Rejected for this hardening pass because it would combine an internal
  architecture cleanup with a broad parser/matcher migration. The ADR-0031
  field model is in place; this PR only makes the implementation harder to
  misuse.
- **Push all identity behavior into Fleet upload.** Rejected because Fleet is
  only one consumer. `scan bom`, OSV federation, and local matching need the
  same match-coordinate semantics before any upload occurs.
- **Treat `install_source` as a graph identity source.** Rejected because
  launcher argv is install context. It can derive package match coordinates for
  unpinned `npx`/`uvx` launches, but it must not become the component's graph
  occurrence identity.

## Consequences

The scanner has one internal place to update when MCP launcher semantics or
identity matching rules change. Tests can exercise a matrix of component
families against the helper API, then sink tests only need to prove that each
sink delegates to the helper.

The cost is one more domain module and a small amount of re-export plumbing for
existing imports from `tools.component_ref`. Pre-V0 compatibility allows that
internal move; external BOM fields and CLI behavior stay unchanged.

## When to revisit

Revisit if `ComponentRef` itself is replaced by a richer internal IR with
separate typed fields for graph identity, match coordinate, package coordinate,
BOM row reference, and agent host. At that point these helpers should become
methods or constructors on that IR rather than free functions.
