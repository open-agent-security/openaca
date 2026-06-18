---
status: accepted
date: 2026-06-17
---

# ADR-0033: Separate scanner observations from advisories and posture

## Context

OpenACA currently emits two finding families:

- vulnerability findings, matched from upstream advisory records
- posture findings, emitted by OpenACA configuration-hygiene rules

Skills and other mutable agent artifacts also need audit evidence from local
content scanners or third-party audit services. These results are not
vulnerability advisories: they may be heuristic, source-specific, and tied to
the exact artifact bytes observed during a scan. Treating them as OpenACA
advisories would imply a durable verdict and disclosure process that OpenACA
does not operate today.

## Decision

OpenACA will model scanner and third-party audit results as
**observation findings**.

Observation findings are attributed evidence, not OpenACA verdicts. They must
carry the source that produced the observation, the source version when known,
confidence, severity, subject coordinate, component identity, evidence, and
remediation. User interfaces and policy engines may act on observations, but
must preserve source attribution such as "OpenACA skill audit observed..." or
"SkillSpector observed..." rather than presenting them as OpenACA advisories.

The three evidence families are:

1. **Advisories**: upstream vulnerability or malicious-package records matched
   by package, Git, or explicit match coordinates. Upstream owns severity,
   affected ranges, and fixes; OpenACA overlays add agent context.
2. **Posture findings**: OpenACA scanner rules for configuration hygiene. These
   are scanner-side policy signals, not advisories.
3. **Observation findings**: scanner or audit-source observations about an
   observed artifact. These are source-attributed audit evidence, not durable
   advisory records.

Skill artifact coordinates are first-class because they support deduplication,
change detection, provenance, audit history, and policy exceptions. OpenACA
will use one canonical skill artifact coordinate: a deterministic
`skill-content-hash` over the normalized skill directory, not a `SKILL.md`-only
file hash or a Git tree SHA. Advisory lookup by those coordinates is optional
future upside, not the primary purpose.

## Consequences

- OpenACA overlays remain conservative and score-free.
- Observation sources can be added incrementally behind a common normalized
  shape.
- Downstream consumers can persist and policy-evaluate observations without
  mixing them into vulnerability counts.
- False positives are contained by attribution and confidence instead of being
  laundered into OpenACA-owned verdicts.

## Rejected

- **Mint OpenACA advisory records for every scanner hit.** This would overstate
  heuristic findings and require a research/disclosure operation before the
  project has earned that authority.
- **Fold observations into posture findings.** Posture findings are OpenACA
  rule verdicts. Observations may come from external scanners and need explicit
  source attribution.
- **Use skill coordinates only for vulnerability lookup.** Stable skill
  coordinates are useful even when no vulnerability database exists.
