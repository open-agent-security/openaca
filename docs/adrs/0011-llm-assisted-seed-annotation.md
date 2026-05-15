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

OpenACA keeps deterministic discovery and alias deduplication, but supports
opt-in LLM annotation through explicit provider settings:
`openaca seed --llm-provider <openai|anthropic> --llm-model <name>`.
The API key comes from `--llm-api-key` or `OPENACA_LLM_API_KEY`. The seeder
loads `docs/frameworks/*.md`, passes those
framework summaries, the OSV record, and a neutral annotation schema to
the provider, and expects a JSON OpenACA annotation in the response. In LLM
mode, the LLM owns the OpenACA annotation; deterministic classification is
not used as a fallback or merge source. Invalid LLM output fails
candidate generation instead of writing a suspect candidate.

Canonical publication remains unchanged: generated files are candidates
only, and a human still promotes reviewed candidates through
`openaca promote`. LLM annotation is a drafting aid, not publishing
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
- **Use an arbitrary local command hook**: rejected because the review
  workflow is clearer when the seed command takes the provider, model,
  and API key directly. First-party adapters for OpenAI and Anthropic
  are enough for V0 and remain testable without network access.
- **Support every hosted LLM provider**: rejected for V0. The project
  only needs the two providers maintainers expect to use immediately;
  additional providers can be added when there is a concrete need.
- **Auto-promote high-confidence LLM output**: rejected for V0. The
  framework docs improve drafting quality, but OpenACA credibility still
  depends on human-reviewed canonical overlays.

## Consequences

The normal seed path remains usable without any LLM dependency. Maintainers
who want framework-grounded drafts provide a provider, model name, and API
key. Provider adapters are small and tested with mocked HTTP calls, so CI
does not need network access.

The downside is that adding a new provider now requires code instead of a
wrapper script. Candidate YAMLs still need review, and reviewers should
treat LLM evidence as a drafting aid rather than proof.

## When to revisit

Revisit when OpenACA standardizes on a replayable LLM-recording harness, or
when candidate volume justifies stricter automation such as verifier-based
bucketing. Revisit the provider list when maintainers need a third hosted
provider or a local model adapter.
