---
id: 0014
title: Schema extension key `database_specific.openaca` (renames namespace from `asve`)
status: accepted
date: 2026-05-15
supersedes: 0002
superseded-by: null
---

## Context

ADR-0002 locked the `database_specific.asve` extension key from V0 onward.
That ADR's "no renames once advisories use it" rule explicitly assumed at
least one published advisory had been minted using the key. As of
2026-05-15, V0 has not launched, no advisories have been published under
the prior project name, and no external consumer has cached records using
the `asve` key. The cost of renaming the namespace today is zero; the
cost of renaming it after V0 launch would be high.

The project also renamed from ASVE (Agent Stack Vulnerabilities and
Exposures) to OpenACA (Open Agent Composition Analysis) to reflect the
agent-composition-analysis category framing. Keeping the wire key as
`asve` after the brand rename would leave a stale namespace string baked
into every overlay forever.

## Decision

OpenACA-specific fields live under `database_specific.openaca`. The
schema's `$defs/openaca_extension` and `$defs/openaca_taxonomies` blocks
are the canonical surface. The key is locked from V0 onward; no further
renames once V0 ships and external consumers begin caching records.

## Alternatives considered

- **Keep `database_specific.asve` despite the brand rename.** Rejected:
  the project name is `openaca` everywhere else (CLI, package, schema
  filename, URL); leaving the wire key as `asve` would be permanent
  noise in every published overlay and would confuse new readers about
  the project's identity.
- **Use a more generic key (e.g., `database_specific.agentic`).**
  Rejected for the same reason ADR-0002 rejected it: too generic; would
  block any other agentic-security project from using the same key
  without collision.

## Consequences

- All overlay schema additions go under `database_specific.openaca`.
  OSV-compliant consumers ignore the extension; OpenACA-aware tooling
  reads it.
- ADR-0002's lock-from-V0 rule rolls forward: this rename is the last
  pre-V0 namespace change. After V0 ships, downstream consumers will
  cache records under the new key, and changing it again would break
  every consumer.
- One-time migration cost: every file in the corpus, every test
  fixture, and every doc that referenced `database_specific.asve`
  updated to `database_specific.openaca` in a single commit.

## When to revisit

If OSV's schema evolves to absorb agentic-context fields natively
(e.g., a top-level `agent_context` block in OSV), migrate OpenACA
records to use the native fields and deprecate
`database_specific.openaca`. Until then, the extension key is
permanent.
