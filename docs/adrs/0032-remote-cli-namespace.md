---
id: 0032
title: Use remote as the public upload CLI namespace
status: accepted
date: 2026-06-09
supersedes: 0024
superseded-by: null
---

## Context

ADR-0024 chose an explicit `openaca fleet ...` command group for opt-in endpoint
inventory upload. The opt-in upload boundary remains correct, but the public CLI
name couples the OSS scanner to one hosted product surface and can be confused
with endpoint-management products that already use "fleet" as their primary
name.

The command group also contains more than a literal BOM upload. It configures a
remote API, checks token and asset status, registers or reuses an endpoint
asset, scans local agent configuration, builds an Agent BOM plus posture
findings, sanitizes the upload payload, and syncs it to the configured backend.

## Decision

OpenACA uses `openaca remote ...` as the public CLI namespace for networked
backend interactions. The endpoint collection command is
`openaca remote sync endpoint`. Local `scan` and `bom` commands remain
upload-free. Backend URLs remain configurable, and internal implementation
modules may continue to use Fleet-specific names when they refer to the hosted
backend implementation.

## Alternatives considered

- **Keep `openaca fleet ...`**: rejected because it couples the OSS command
  surface to one hosted product name and is easy to misread as a FleetDM
  integration.
- **Use `openaca bom upload ...` for endpoint collection**: rejected because
  endpoint sync is not just artifact upload. It also registers/reuses an asset,
  scans local agent configuration, runs posture checks, and handles pending
  upload caching.
- **Use `openaca monitor ...`**: rejected because "monitor configure" reads
  awkwardly and implies an always-running monitor, while V0 uses explicit CLI
  runs scheduled by launchd or MDM.
- **Use `openaca upload ...` or `openaca push ...` as top-level verbs**:
  rejected because configure and status do not fit naturally under an upload
  verb, and future compatible backends should share one configured remote
  namespace.

## Consequences

The public CLI is product-neutral and better describes the configured remote
relationship. The endpoint workflow reads as a state synchronization operation:
`openaca remote sync endpoint`.

The internal Python module names and backend protocol can still use Fleet names
where that is the implementation being contacted. That keeps this decision
focused on the user-facing OSS command surface rather than forcing an unrelated
internal package rename.

If OpenACA later supports literal upload of an existing BOM artifact, that
operation should live under the same remote namespace, for example
`openaca remote upload bom <path>`.

## When to revisit

Revisit if OpenACA adds multiple named remote backends with materially different
protocols. At that point `remote` may need profiles or provider-specific
subcommands, but the product-neutral namespace should remain the default.
