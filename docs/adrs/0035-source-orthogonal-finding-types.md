---
status: accepted
date: 2026-06-18
supersedes: 0034
superseded-by: null
---

# ADR-0035: Classify findings by claim type; keep source attribution orthogonal

## Context

ADR-0034 established the scanner-specific adapter boundary for external skill
and content scanners, but it treated external scanner output as observations by
default. That conflates two independent concepts:

- what kind of claim a finding makes
- who made the claim

The existing vulnerability model already keeps these concepts separate. A
finding matched from OSV or GHSA is a vulnerability because it matches an
advisory or malicious-package record, not because OpenACA produced the claim.
The same rule should apply to posture and observation findings.

## Supersedes ADR-0034

This ADR supersedes ADR-0034's statement that external skill/content scanner
results are observations by default. It carries forward ADR-0034's adapter
boundary unchanged: OpenACA does not implement a generic SARIF semantics engine;
scanner-specific adapters normalize the subset each scanner actually emits.

It also carries forward the `skill-content-hash` coordinate decision from
ADR-0034.

## Decision

OpenACA classifies findings by **claim type**. Source attribution is an
orthogonal field on every finding family.

The finding families are:

1. **Vulnerability**: the component matches a known advisory or malicious-package
   record. A scanner result only becomes a vulnerability finding when it carries
   a concrete advisory identity or match coordinate.
2. **Posture**: the component, host, or BOM is configured, permitted,
   provenanced, admitted, or scoped in a risky way.
3. **Observation**: an audit source observed content or behavior evidence inside
   an artifact.

`source` identifies who made the claim: `openaca`, `osv.dev`, `skillspector`,
or another scanner/audit source. `source_version`, `confidence`, `evidence`,
and taxonomy fields travel with the finding where available. OpenACA-native
deterministic posture findings use `source: openaca`; external posture findings
keep their external source instead of being relabeled as OpenACA verdicts.

Classification is by the claim, not by the evidence surface. `SKILL.md`
frontmatter can feed either family:

- `allowed-tools` grants executable capability: posture
- `description` contains prompt-injection bait: observation

OpenACA's native `skill.allowed-executable-tool` check is therefore reclassified
as `openaca-posture-skill-executable-tool`: an OpenACA posture finding about
skill capability scope.

## Consequences

- Adapters classify scanner rules per claim type instead of placing all scanner
  output in observations.
- Posture findings carry source attribution alongside confidence and evidence.
- User interfaces and policy engines can aggregate by claim type while still
  showing who made the claim.
- Scanner output that reports an advisory ID can dedupe against vulnerability
  findings instead of appearing as a parallel observation.
- Native OpenACA content/behavior checks, if added, are observations; native
  OpenACA configuration/capability/provenance checks are posture.

## Rejected

- **All external scanner output is an observation.** This preserves attribution,
  but makes claim type depend on source. It also makes scanner-reported
  advisories and scanner-reported configuration issues special cases.
- **All frontmatter checks are posture.** Frontmatter is an evidence surface, not
  a claim type. A capability declaration is posture; suspicious instruction text
  is observation.
- **All OpenACA-native checks are posture.** OpenACA can emit observations when
  the claim is about artifact content or behavior. Determinism is not the
  separator.
