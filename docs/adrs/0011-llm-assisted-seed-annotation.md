---
id: 0011
title: Add opt-in LLM seed annotation with framework context
status: accepted
date: 2026-05-13
supersedes: 0010
superseded-by: null
---

## Context

ADR-0010 kept V0 seeding deterministic because the initial candidate
volume looked small enough for manual review. After adding curated
framework references for OWASP Agentic AI, OWASP MCP, OWASP Agentic
Skills, OWASP LLM, and MITRE ATLAS, deterministic classification became
the weaker part of the workflow. It can find MCP-related candidates, but
it cannot reliably reason across advisory details and framework
definitions. For example, a malicious package or command-execution
advisory may map differently depending on whether the agent behavior is
supply-chain compromise, prompt injection, tool misuse, credential
harvesting, or host escape.

## Decision

ASVE keeps deterministic discovery and alias deduplication, but supports
opt-in LLM annotation through `asve-seed --llm-command`. The seeder loads
`docs/frameworks/*.md`, passes those framework summaries, the OSV record,
and a neutral annotation schema to the command over stdin, and expects a
JSON ASVE annotation on stdout. In LLM mode, the LLM owns the ASVE
annotation; deterministic classification is not used as a fallback or
merge source. Invalid LLM output fails candidate generation instead of
writing a suspect candidate.

Canonical publication remains unchanged: generated files are candidates
only, and a human still promotes reviewed candidates through
`asve-promote`. LLM annotation is a drafting aid, not publishing
authority.

## Alternatives considered

- **Keep deterministic annotation only**: rejected because framework
  mapping requires semantic reasoning that simple keyword rules handle
  poorly. The candidate finder can remain deterministic without requiring
  the classification to be deterministic.
- **Merge LLM output into deterministic annotations**: rejected because
  heuristic mappings can silently survive even when the LLM would have
  omitted or contradicted them. In LLM mode, missing required fields
  should fail validation and force review.
- **Hard-code a hosted LLM provider**: rejected because seeding is a
  maintainer workflow and provider credentials do not belong in the
  project. A command interface keeps the repo provider-neutral and easy
  to test with local fixtures.
- **Auto-promote high-confidence LLM output**: rejected for V0. The
  framework docs improve drafting quality, but ASVE credibility still
  depends on human-reviewed canonical overlays.

## Consequences

The normal seed path remains usable without any LLM dependency. Maintainers
who want framework-grounded drafts can provide a local command that wraps
their model of choice. The command boundary also makes the behavior
testable without network access.

The downside is that prompt and provider behavior live outside this repo
for now. Reproducibility depends on the wrapper command the maintainer
uses. Candidate YAMLs still need review, and reviewers should treat LLM
evidence as a drafting aid rather than proof.

## When to revisit

Revisit when ASVE standardizes on a checked-in prompt and replayable
LLM-recording harness, or when candidate volume justifies stricter
automation such as verifier-based bucketing. Revisit provider neutrality
only if maintaining external wrapper commands becomes more costly than
supporting a small first-party provider adapter.
