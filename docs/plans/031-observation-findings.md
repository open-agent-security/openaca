# Plan 031: Observation Findings

Status: complete

## Goal

Add a third evidence family for source-attributed scanner/audit observations,
starting with deterministic skill observations and preserving stable skill
tree artifact coordinates for dedupe, drift, and policy history.

## Tasks

- [x] Document observation semantics in ADR-0033.
- [x] Add OSS `ObservationFinding` model and deterministic skill observation source.
- [x] Add skill tree artifact coordinates to Agent BOM component properties.
- [x] Include observations in JSON/GitHub/SARIF output and remote uploads.
- [x] Extend remote upload schema and privacy validation for observation evidence.
- [x] Add focused tests for OSS output formats.
