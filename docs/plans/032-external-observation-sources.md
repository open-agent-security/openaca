# Plan 032: External Observation Sources

Status: complete

## Goal

Establish the durable scanner-source boundary — the `ObservationFinding`
model, the `skill-content-hash` coordinate, and the ADR-0034/0035 decisions —
so external skill scanner evidence can be normalized with source attribution
and classified by claim type, without building a broad native content scanner
or a generic "handle any valid SARIF" engine. Per-scanner adapters land in
follow-up plans.

## Tasks

- [x] Document the scanner-source boundary in ADR-0034, including that
      scanner-specific adapters own normalization and OpenACA does not implement
      general SARIF semantics.
- [x] Define the `ObservationFinding` model (source, source version, rule ID,
      severity, confidence, evidence, categories, subject coordinate, component
      identity).
- [x] Establish the `skill-content-hash` coordinate (renamed from
      `skill-tree-hash`).
- [x] Keep native, deterministic OpenACA skill observations within the model.

## Deferred

- [ ] Add a SkillSpector-specific adapter against SkillSpector's actual output,
      with deterministic rule -> taxonomy mapping (next plan / PR #127).
- [ ] Add later scanner-specific adapters (e.g. Cisco, bawbel) as needed.
- [ ] Run candidate scanners against a shared fixture corpus and compare noise.
- [ ] Add cross-scanner dedupe/disagreement handling.
- [ ] Add structured OWASP Agentic Skills Top 10 taxonomy fields if category
      strings become insufficient.
