---
id: 0025
title: Treat Fleet upload as endpoint inventory with a narrow hygiene contract
status: accepted
date: 2026-05-28
supersedes: 0024
superseded-by: null
---

## Context

ADR-0024 made the right command-boundary decision: Fleet collection is explicit
and opt-in under `openaca fleet ...`; local scan and BOM flows do not upload.
It also required local redaction validation before upload and pending-cache
write.

That second part became the wrong abstraction. The Fleet collector is endpoint
inventory. Its value is showing which agent components, manifests, install
references, posture findings, and runtime host labels exist across a developer
fleet. A broad "sanitize everything sensitive" promise made normal inventory
fields look like bugs, created per-field allowlists, and caused repeated
review churn around paths and source metadata that the product is meant to
collect.

At the same time, Fleet should not accidentally upload obvious high-risk data:
source code, raw config file bodies, environment variable values, reusable
secrets, or full shell argv.

## Decision

Fleet upload remains explicit and opt-in, but the upload contract is endpoint
inventory, not comprehensive sanitization.

Fleet may upload:

- Agent BOM component identities, PURLs, names, versions, and component types.
- OpenACA component properties such as source manifest, source locator,
  install reference, transport, pinning state, provenance status, and
  attribution metadata.
- Posture finding records produced by the local posture engine.
- Runtime host labels and asset metadata needed to identify the endpoint in
  the Fleet dashboard.
- Pending upload cache files containing the same payload that would have been
  sent over HTTPS.

Fleet must not upload:

- Source code.
- Raw config file bodies such as an entire `settings.json`, `mcp.json`, or
  plugin manifest body.
- Environment variable values.
- Detected tokens, API keys, passwords, bearer credentials, or similar
  reusable secrets.
- Full shell argv or command argument lists.

The collector enforces this with a small final-payload guard at the two sinks:
HTTPS upload and pending-cache write. The guard rejects forbidden key/property
names and known secret-like values without echoing the value in error messages.
It does not reject paths, component identities, install references, or benign
URL query strings merely because they may be identifying inventory metadata.

`openaca fleet upload <bom-path>` is deferred from V0. The endpoint collector
owns the V0 payload shape; accepting arbitrary externally generated BOM files
would make the contract harder to enforce and explain.

## Alternatives considered

- **Comprehensive redaction before upload/cache**: rejected because it treats
  ordinary endpoint inventory as suspect, requires growing allowlists, and has
  already produced repeated review churn.
- **No client-side hygiene guard**: rejected because accidentally uploading
  raw configs, env values, secrets, or full argv is high impact and cheap to
  block at the final payload sinks.
- **Manual BOM upload in V0**: rejected because arbitrary BOM input expands
  the contract surface before the endpoint collector path has stabilized.
- **Relative-path rewriting before upload**: rejected as a privacy control.
  Paths are endpoint inventory. If a future extractor should emit relative
  paths for clarity, it should do so at extraction time, not as an upload-time
  sanitization pass.

## Consequences

The implementation is simpler: one narrow guard replaces redaction helpers and
per-rule posture evidence allowlists. Fleet payloads are easier to document and
debug because the uploaded BOM is the endpoint inventory the collector produced.

The product must describe the data surface honestly. Fleet is not a
privacy-preserving transform over arbitrary local files; it is an opt-in
endpoint inventory upload with a small high-risk-data exclusion list.

Manual upload can return after V0 if the project defines a stable validation
contract for externally supplied BOM files.

## When to revisit

Revisit if design partners require stronger local minimization than endpoint
inventory disclosure, if manual BOM upload becomes a required workflow, or if
the forbidden-data guard causes recurring false positives in legitimate Fleet
payloads.
