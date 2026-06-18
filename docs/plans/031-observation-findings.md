# Plan 031: Observation Findings

Status: complete

## Goal

Add a third evidence family for source-attributed scanner/audit observations,
starting with deterministic skill observations and preserving stable skill
artifact coordinates for dedupe, drift, and policy history.

## Tasks

- [x] Document observation semantics in ADR-0033.
- [x] Add OSS `ObservationFinding` model and deterministic skill observation source.
- [x] Add skill artifact coordinates to Agent BOM component properties.
- [x] Include observations in JSON/GitHub/SARIF output and remote uploads.
- [x] Extend Cloud upload schema, privacy validation, ingest, dashboard summaries,
      inventory rows, findings page, and policy evaluation.
- [x] Add focused tests for OSS output and Cloud ingestion/display.
