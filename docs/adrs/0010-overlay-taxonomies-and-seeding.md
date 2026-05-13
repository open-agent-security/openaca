---
id: 0010
title: Use deterministic candidate seeding and nested overlay taxonomies
status: accepted
date: 2026-05-13
supersedes: null
superseded-by: null
---

## Context

ADR-0009 changed ASVE V0 from a vulnerability database to an
agent-context overlay corpus. The next scaling problem is corpus growth:
OSV bulk dumps contain existing GHSA, CVE, PYSEC, and MAL records for
MCP and other agent-stack components, but those records do not carry
ASVE-specific context such as agent surfaces, agent impact, and
agent-specific taxonomies. We need tooling to find candidate overlays
without letting unreviewed or upstream-owned data enter `overlays/`.

The existing flat `owasp_agentic_top10` field also does not leave a clean
place for related open taxonomies such as OWASP MCP Top 10, OWASP
Agentic Skills Top 10, OWASP LLM Top 10, and MITRE ATLAS.

## Decision

ASVE V0 uses a deterministic seeding workflow. Seeders read OSV bulk
dumps, apply rule-based discovery/classification heuristics, and write
reviewable files under `candidates/`. Candidate files may include
review-only metadata and upstream excerpts, but canonical `overlays/`
files remain minimal and scanner-visible. Promotion is an explicit human
step through `asve-promote`, which projects a candidate into the
canonical overlay shape and validates it before writing
`overlays/<upstream-id>.yaml`.

ASVE-specific taxonomy mappings live under
`database_specific.asve.taxonomies`. `owasp_agentic_top10` moves into
that block, and optional taxonomy families can be added alongside it.
CWE is not duplicated by default because upstream CVE/GHSA/OSV records
already commonly carry CWE mappings. If ASVE adds a reviewed supplemental
mapping later, it belongs under `taxonomies.supplemental_taxonomies`, not
as an implicit override of upstream data. Malicious-package overlays use
`database_specific.asve.threat_kind: malicious_package`; they do not use
a top-level `type: malicious_package`.

## Alternatives considered

- **Live LLM annotation in V0**: rejected as premature. The initial MCP
  backlog is small enough for human review, and live LLM support would
  add provider configuration, prompt maintenance, replay fixtures,
  prompt-injection controls, and provenance semantics before the
  deterministic candidate boundary has proven useful.
- **Write candidates under `overlays/_candidates/`**: rejected because
  scanner/export tooling recursively loads `overlays/`. Unreviewed
  candidates must not be scanner-visible.
- **Move candidate files directly into `overlays/` on approval**:
  rejected because candidates may contain `_candidate` review metadata,
  upstream summaries/details, and evidence excerpts. Promotion must
  project into the canonical shape instead of moving files.
- **Add `type: malicious_package`**: rejected for V0 because top-level
  record type is part of upstream vulnerability-record semantics. ASVE's
  overlay-specific classification belongs inside `database_specific.asve`.
- **Duplicate CWE in every overlay taxonomy block**: rejected because CWE
  is upstream-owned for aliased records. Duplicating it creates drift
  without adding agent context.

## Consequences

The V0 seeding workflow is intentionally slower than auto-publishing but
keeps the trust boundary simple: no overlay enters the canonical corpus
without a human commit. The deterministic seeder can still accelerate
backlog discovery, and the candidate/promote boundary leaves room for
future LLM-assisted review without changing the canonical overlay
contract.

Nested taxonomies make overlay metadata more extensible, but consumers
must update from the flat `owasp_agentic_top10` field to
`taxonomies.owasp_agentic_top10`. V0 is pre-launch, so this wire-format
change is acceptable.

## When to revisit

Revisit live LLM annotation when candidate volume makes manual review the
primary bottleneck, or when ASVE expands into enough additional
agent-stack ecosystems that deterministic candidates regularly exceed
human review capacity. Revisit CWE handling if upstream records lack CWE
coverage for a class of ASVE-native overlays.
