---
id: 0002
title: Schema extension key `database_specific.asve`
status: accepted
date: 2026-05-06
supersedes: null
superseded-by: null
---

## Context

OSV's schema reserves `database_specific` for per-database extension. ASVE's
agent-context overlay (`component_type`, `surfaces`, `agent_impact` boolean
table, OWASP ASI mapping, `evidence_level`) needs a stable key under that
namespace. Once advisories ship referencing the key, renaming would break
every downstream consumer that has cached ASVE records.

## Decision

ASVE-specific fields live under `database_specific.asve`. The key is locked
from V0; no renames once advisories use it.

## Alternatives considered

- **`database_specific.agentvulndb`** — older candidate name from a previous
  iteration of the project plan. Rejected once project name finalized as
  ASVE; using a stale name in the wire format would force an irreversible
  rename later.
- **Top-level keys (e.g., `asve_metadata`)** — would break OSV consumers
  that validate against the canonical OSV schema (additionalProperties
  rejection at the top level).
- **`database_specific.agentic`** — too generic; would block any other
  agentic-security project from using the same key without collision.

## Consequences

- All ASVE schema additions go under the single `asve` extension key.
- OSV-compliant consumers ignore the extension; ASVE-aware tooling reads it.
- The schema's `$defs/asve_extension` block is the canonical surface; any
  new agent-context field is added there, not at top level.

## When to revisit

If OSV's schema evolves to absorb agentic-context fields natively (e.g., a
top-level `agent_context` block in OSV), migrate ASVE records to use the
native fields and deprecate `database_specific.asve`. Until then, the
extension key is permanent.
