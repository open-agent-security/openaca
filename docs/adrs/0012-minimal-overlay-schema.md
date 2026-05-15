---
id: 0012
title: Keep canonical overlays minimal and standards-based
status: accepted
date: 2026-05-14
supersedes: null
superseded-by: null
---

## Context

OpenACA has pivoted from a standalone advisory database to an overlay layer on
top of upstream OSV/GHSA/CVE records. In that model, upstream records own
vulnerability facts such as package identity, affected ranges, severity,
summary, details, references, aliases, and CWE. The scanner owns local
observation context such as whether a vulnerable package was discovered via
an MCP server declaration, a Claude skill, a hook, a command, an agent, or a
plugin.

The seed pipeline exposed a schema problem: LLM-assisted candidates produced
free-form values for `component_type`, `surfaces`, `agent_impact`, and
`threat_kind`. The validator accepted those values because the schema allowed
plain strings and arbitrary boolean impact keys. Tightening those fields into
OpenACA-owned enums would still leave OpenACA maintaining a custom taxonomy that
duplicates either scanner observation context or upstream vulnerability
description. The MVP bar is stricter: every canonical overlay field needs a
clear reason to exist, and standards-based fields are preferred.

## Decision

Canonical overlays keep only OpenACA's reviewed agent-security taxonomy mapping
and review confidence under `database_specific.openaca`. The required canonical
OpenACA fields are `taxonomies` and `evidence_level`. `taxonomies` contains the
supported standards-based framework mappings: OWASP Agentic Top 10, OWASP MCP
Top 10, OWASP Agentic Skills Top 10, OWASP LLM Top 10, MITRE ATLAS, and
reviewed supplemental mappings when needed. `evidence_level` records OpenACA's
confidence in the overlay mapping, not upstream vulnerability confidence.

Canonical overlays do not carry `component_type`, `surfaces`, or
`agent_impact`. The scanner reports observed component context at scan time;
the overlay does not constrain or restate that observation. `threat_kind`
remains optional and enum-constrained for OpenACA-specific overlay subtypes that
upstream OSV does not model cleanly; the only V0 value is
`malicious_package`. Ordinary vulnerability overlays omit `threat_kind`.

Canonical overlays do not carry `component_identity`. In the pure overlay
MVP, matching is driven by upstream package/version data and aliases. Local
or identity-only agent components remain scanner inventory concepts unless
OpenACA later reintroduces OpenACA-native records through a separate decision.

Candidate files may keep review-only metadata such as matched heuristics,
LLM decisions, rejection reasons, evidence quotes, and scanner-observed
component hints. Promotion strips that metadata and writes only the canonical
overlay projection.

## Alternatives considered

- **Keep `component_type` as a canonical enum**: rejected because it mixes
  scanner-observed context with advisory classification. If a scanner sees a
  package through a skill but the overlay classifies the advisory as a
  command, the field either becomes confusing informational metadata or an
  unsafe match constraint.
- **Split `component_type` into `discovery_surface` and `component_role`**:
  rejected for MVP because it adds schema without a current consumer.
  Scanner output already has discovery surface, and gateway/proxy/server role
  distinctions can be added later if reports need them.
- **Keep `surfaces`**: rejected because the term is ambiguous. It can mean
  the local manifest surface that exposed the component, the vulnerable code
  path, or the attacker interaction surface. The first belongs to scanner
  output; the others are already described by upstream details and framework
  mappings.
- **Keep `agent_impact` booleans**: rejected because the keys are OpenACA-owned
  and not mapped directly from a supported standard. They risk becoming a
  second custom taxonomy beside OWASP and MITRE without a proven MVP
  reporting need.
- **Use `threat_kind: vulnerability | exposure`**: rejected because those are
  top-level record-type concepts. OpenACA V0 overlays upstream vulnerability
  records, and `exposure` / `config` records remain out of V0 scope.
- **Keep `component_identity` as an overlay matching key**: rejected for MVP
  because OSV-backed overlays should match through upstream
  `affected[*].package` data and aliases. Local hooks, commands, and agents
  are scanner inventory items, not upstream advisory identities. Reintroducing
  OpenACA-native identity-based records would require a separate ADR.
- **Let LLM rejected candidates disappear**: rejected because the LLM should
  not silently control corpus coverage. Rejections belong in reviewable
  candidate or run artifacts.

## Consequences

The canonical overlay schema becomes smaller and easier to validate. LLM
annotation has less room to invent project-specific labels, because the
canonical schema only accepts framework mappings, evidence level,
`malicious_package` when applicable, and optional identity matching keys.

Scanner output becomes the place where discovered component context is shown.
Reports combine local scan observations with upstream advisory data and
OpenACA-reviewed taxonomy mappings instead of relying on canonical overlays to
describe all three.

Existing overlays and tests must be migrated. Renderers and static export
templates must stop displaying component type, surfaces, and impact from
overlay metadata. Seed candidates and LLM annotation tests must be updated so
generated canonical annotations are minimal, and rejected LLM decisions are
kept auditable.

The downside is that OpenACA loses a custom impact summary in MVP output. If
users later need OpenACA-owned impact facets for filtering or policy decisions,
those facets should be added by a new ADR with a concrete consumer and a
closed vocabulary.

## When to revisit

Revisit if scanner/report consumers need a stable filter that cannot be
derived from scan observations, upstream OSV fields, or supported framework
taxonomies. Revisit identity-based matching only if OpenACA explicitly adds
OpenACA-native records for local agent components.
