---
status: superseded
date: 2026-06-18
supersedes: 0033
superseded-by: 0035
---

# ADR-0034: Use external scanners as observation sources

## Context

ADR-0033 separates observation findings from vulnerability advisories and
OpenACA posture findings. The next design question is where detection logic for
skill-content risks belongs.

Several existing tools already scan AI agent skills and related artifacts for
content risks such as prompt injection, data exfiltration, excessive agency,
unsafe tool use, secret access, and supply-chain drift. Some emit SARIF or JSON,
and each has its own rule IDs, severity model, confidence model, taxonomy, and
evidence shape.

OpenACA also has context those scanners do not own: Agent BOM identities,
artifact coordinates, source provenance, component paths, advisory matching,
posture findings, and downstream policy/evaluation history.

## Supersedes ADR-0033

This ADR carries forward ADR-0033's separation of observation findings from
vulnerability advisories and posture findings unchanged; that three-family
rationale remains the reference for *why* observations are a distinct family. It
supersedes ADR-0033 only to (1) rename the canonical skill coordinate kind from
`skill-tree-hash` to `skill-content-hash` (see Decision) and (2) record where
external-scanner detection belongs.

## Decision

OpenACA will treat external skill/content scanners as **observation sources**.
OpenACA will not build a broad native skill-content scanning engine in V0.

The OpenACA responsibility boundary is:

- attach scanner results to Agent BOM component identities and artifact
  coordinates
- preserve scanner source, source version, rule ID, severity, confidence, and
  evidence
- map scanner-specific categories into OpenACA-supported taxonomy labels only
  through explicit adapter mappings
- deduplicate and policy-evaluate observations without converting them into
  OpenACA advisory records
- keep native OpenACA skill observations limited to deterministic facts OpenACA
  can observe directly, such as declared executable tool access and artifact
  coordinates
- rename the canonical skill coordinate kind from `skill-tree-hash` (ADR-0033)
  to `skill-content-hash`: the coordinate hashes normalized skill-directory
  content, not a Git tree object, and the new name removes the implied Git
  dependency

SARIF is accepted as an interchange format, not as a semantic contract. OpenACA
does not implement general SARIF semantics. Instead, **scanner-specific adapters**
(e.g. a SkillSpector adapter) own source normalization against the subset each
scanner actually emits — mapping rule identity, source attribution,
severity/confidence, location, evidence, component identity, subject coordinate,
and taxonomy categories. Shared SARIF-reading helpers may exist as internal
utilities, but a generic "handle any valid SARIF" adapter is explicitly not the
product boundary.

## Consequences

- OpenACA can integrate scanner coverage incrementally without inheriting every
  detector as OpenACA-owned logic.
- Scanner disagreement remains visible because observations retain source
  attribution.
- OpenACA avoids presenting heuristic scanner output as durable vulnerability
  advisories.
- Additional adapters can be added without changing the core
  `ObservationFinding` model.
- OpenACA's native observation rules stay conservative and explainable.

## Rejected

- **Build broad native skill-content detectors now.** This duplicates existing
  scanner work and would require an ongoing research/evaluation operation before
  OpenACA has established that authority.
- **Treat SARIF ingestion as sufficient by itself.** SARIF moves results between
  tools, but does not define OpenACA identity semantics, taxonomy mappings,
  confidence rules, or safe evidence handling.
- **Ship a generic, fully-conformant SARIF adapter as the boundary.** Valid SARIF
  is an unbounded surface (tool extensions, hierarchical rule IDs, configuration
  overrides, baseline/suppression state, localized message strings), while each
  real scanner emits a narrow, stable subset. A per-scanner adapter is smaller,
  testable against that scanner's actual output, and gives a principled boundary;
  a generic adapter invites endless edge-case maintenance for shapes no integrated
  scanner emits.
- **Convert scanner hits into OpenACA advisory records.** Scanner observations
  are tied to source, configuration, and artifact bytes. Advisory records need
  durable affected ranges, disclosure process, and upstream ownership.
- **Fold external scanner output into posture findings.** Posture findings are
  OpenACA rule verdicts. External scanner output must keep source attribution.
