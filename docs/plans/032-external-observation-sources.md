# Plan 032: External Observation Sources

Status: complete

## Goal

Add the first adapter boundary for third-party scanner results so OpenACA can
normalize external skill scanner evidence as source-attributed observations
without building a broad native content scanner.

## Tasks

- [x] Document the scanner-source boundary in ADR-0034.
- [x] Add a generic SARIF observation adapter.
- [x] Preserve scanner source, source version, rule ID, severity, confidence,
      evidence, and categories.
- [x] Attach external observations to OpenACA component identity and skill
      artifact coordinates.
- [x] Add focused tests for SARIF normalization.

## Deferred

- [ ] Add scanner-specific adapters and taxonomy mappings.
- [ ] Run candidate scanners against a shared fixture corpus and compare noise.
- [ ] Add cross-scanner dedupe/disagreement handling.
- [ ] Add structured OWASP Agentic Skills Top 10 taxonomy fields if category
      strings become insufficient.
