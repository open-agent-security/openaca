---
id: 0022
title: Agent BOM is open, CycloneDX-compatible, and the scanner's composition IR
status: accepted
date: 2026-05-21
supersedes: null
superseded-by: null
---

## Context

OpenACA already discovers agent components as `ComponentRef` values, renders
composition trees, matches package-backed components against OSV records, and
runs scanner-side posture checks against raw manifests. The commercial product
direction needs a durable artifact that can be stored per repo/commit and
re-matched when the public corpus changes. The OSS project also needs a public
artifact that cleanly expresses agent composition without making the BOM itself
a proprietary product boundary.

The established SBOM pattern is that BOM formats and single-target generation
are open, while commercial products operate BOMs across organizations: storage,
history, drift, policy, workflow, and integrations. Closing the Agent BOM
format or local generator would weaken OpenACA's open-substrate claim and make
foundation or standards conversations harder.

## Decision

OpenACA treats Agent BOM as an open composition artifact. The open scanner emits
Agent BOMs for repo and endpoint targets, and regular scans build an Agent BOM
internally before advisory matching. The commercial product may store, diff,
aggregate, and enforce policy on Agent BOMs across organizations, but it does
not own the schema or single-target generation path.

The external interchange format is CycloneDX JSON with OpenACA-owned properties
under the `openaca:*` namespace. OpenACA keeps a focused internal `AgentBOM`
model instead of using raw CycloneDX dictionaries as the domain model. The
internal model serializes to CycloneDX for interchange and can parse enough
CycloneDX back into `ComponentRef` values for `openaca scan bom`.

The Agent BOM is composition-only. It contains target metadata, components,
source provenance, package or OpenACA component identities, and composition
edges. Vulnerability and posture findings are scan report data that reference
BOM component IDs; they are not embedded into the BOM schema. `openaca scan bom`
performs advisory matching against BOM components. It does not replay posture
rules because posture rules require raw configuration files and scanner
evidence that a composition-only BOM intentionally does not preserve.

OpenACA reuses existing component identity rules:

- package-backed components use PURL when available;
- source-less agent components use `ComponentRef.component_identity`;
- component type remains `ComponentRef.extra.component_type`;
- source observation stays in `source_manifest`, `source_locator`,
  `attributed_to`, and selected `extra` provenance fields.

Agent BOM does not mint a custom `pkg:openaca/...` PURL type in V0. If future
standards work needs a formal PURL type or CycloneDX extension, that decision
will get a separate ADR.

## Alternatives considered

- **Closed/proprietary Agent BOM generation**: rejected because it contradicts
  the open-substrate strategy and creates a weaker foundation/compliance story.
- **Native OpenACA-only JSON format**: rejected as the primary interchange
  format because CycloneDX already has broad tooling, a BOM vocabulary, and
  AI/ML BOM support. A small internal model is still useful for code clarity.
- **Raw CycloneDX as the internal model**: rejected because OpenACA needs
  domain concepts such as component identity, attribution, source manifest, and
  composition edges without coupling every parser to CycloneDX dictionary
  details.
- **BOM as a scan side-channel only**: rejected because it would make the BOM a
  presentation artifact rather than the canonical composition IR. Scans should
  build BOMs first and match against their components.
- **Embed findings in the BOM**: rejected because it couples composition to
  OpenACA's findings model and makes third-party BOM consumption harder. A scan
  report may bundle BOM and findings later, but the BOM itself remains
  inventory/composition only.

## Consequences

`openaca bom repo` and `openaca bom endpoint` become first-class OSS commands.
`openaca scan repo` and `openaca scan endpoint` keep their existing UX, but
internally build an Agent BOM before matching. `openaca scan bom` can re-match
a stored BOM against the current corpus without re-reading the original repo or
endpoint config.

Commercial workflows can persist BOMs per repo/commit and re-evaluate them when
new OpenACA overlays or OSV advisories arrive. That enables drift and
"new advisory affects these repos" workflows without immediately re-checking
out every repository.

The initial CycloneDX serialization uses `openaca:*` properties for fields not
covered by CycloneDX core. OpenACA should track CycloneDX and OWASP AI BOM
evolution, but compatibility work should be additive and should not block the
first open Agent BOM release.

## When to revisit

Revisit if CycloneDX standardizes agent-component fields that supersede
`openaca:*` properties, if OSV or PURL gains a formal agent-component identity
scheme, or if posture replay from stored artifacts becomes important enough to
define a separate evidence bundle alongside the BOM.
