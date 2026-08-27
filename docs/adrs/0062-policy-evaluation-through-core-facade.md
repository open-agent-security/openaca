---
id: 0062
title: Expose policy evaluation through the core facade
status: accepted
date: 2026-08-26
supersedes: null
superseded-by: null
---

## Context

A downstream policy consumer needs to validate and preview the same policy
document that endpoint compilation uses. The policy parser and evaluator
already exist in `tools.*`, but ADR-0028 forbids downstream consumers from
importing that internal namespace directly.

## Decision

`openaca.core` re-exports the policy parser, typed policy values, admission and
risk-gate evaluators, and Agent BOM graph reconstruction. A downstream
consumer pins an OpenACA version or commit and consumes only those facade
symbols.

## Alternatives considered

- **Have a downstream consumer import `tools.policy` and `tools.bom` directly.** Rejected
  because it bypasses the supported consumption seam and couples Fleet to
  scanner-internal module layout.
- **Reimplement policy evaluation from Fleet's persisted BOMs.** Rejected
  because policy semantics would drift between preview and endpoint
  compilation.

## Consequences

A downstream consumer can reconstruct an Agent BOM's graph and evaluate policy
with the same logic used by the CLI. The facade remains pre-V0 and intentionally
has no backward-compatibility guarantee; a commit pin and contract tests make
upgrades explicit.

## When to revisit

Revisit when the policy compiler reaches a stable public API or when another
consumer needs a narrower, independently versioned policy interface.
