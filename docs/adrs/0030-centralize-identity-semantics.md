---
id: 0030
title: Centralize identity semantics behind helper APIs
status: accepted
date: 2026-06-08
supersedes: null
superseded-by: null
---

## Context

ADR-0029 made `openaca:identity` the agent graph occurrence key and kept
package/source matching in PURL, Git metadata, and `openaca:source_identity`.
The model is sound, but the implementation still spread identity semantics
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

Identity and source-coordinate semantics live behind shared helper APIs. Parser
outputs still use `ComponentRef`, generated BOMs still emit ADR-0029 fields,
and wire formats do not change, but consumers must call the shared helpers for:

- canonical graph occurrence identity;
- source identity preservation;
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
  architecture cleanup with a broad parser/matcher migration. The public
  ADR-0029 field model is already in place; this PR only makes the
  implementation harder to misuse.
- **Push all identity behavior into Fleet upload.** Rejected because Fleet is
  only one consumer. `scan bom`, OSV federation, and local matching need the
  same source-coordinate semantics before any upload occurs.
- **Treat `install_source` as the source of truth.** Rejected because generated
  BOMs intentionally preserve source-less advisory coordinates in
  `openaca:source_identity`; parsing raw argv is a fallback, not the primary
  matching key.

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
separate typed fields for graph identity, source identity, package coordinate,
BOM row reference, and agent host. At that point these helpers should become
methods or constructors on that IR rather than free functions.
