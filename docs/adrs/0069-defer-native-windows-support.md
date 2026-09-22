---
id: 0069
title: Defer native Windows support
status: accepted
date: 2026-09-21
supersedes: null
superseded-by: null
---

## Context

[ADR-0005](0005-manifest-parsers-posix-only.md) already limits manifest command
classification to POSIX semantics. That decision does not state the host support
policy for the rest of the scanner, library, CLI or development tooling. Leaving
that boundary implicit invites Windows compatibility work one feature at a time.

## Decision

OpenACA targets macOS and Linux hosts. Native Windows execution is deferred
across the library, CLI, endpoint discovery, scanner integrations and development
tooling. New work may use the facilities of those target hosts without adding
Windows substitutes. ADR-0005 remains in force: this extends the explicit scope
to the host runtime without changing manifest classification.

Windows provenance alone is not a reason to reject a manifest, BOM or advisory.
Existing format and identity contracts still apply. Accepting portable data does
not promise Windows path resolution, registry discovery, PATHEXT handling or
Windows-specific component coverage. In particular, this decision does not
expand the parser coverage stated in ADR-0005.

Windows-only compatibility failures do not block a release within this scope.
Record them as deferred rather than adding adapters, fallback implementations or
Windows CI jobs as incidental fixes. A defect that also affects a target host,
input validation or a security boundary remains actionable.

WSL and other compatibility environments have no separate support guarantee.
This decision neither certifies nor prohibits their use.

## Alternatives considered

- **Maintain native Windows parity now.** Rejected because each platform path
  needs maintained tests and operational coverage, beyond the cost of its code.
- **Add compatibility opportunistically.** Rejected because isolated fixes imply
  support without verifying the installation-to-execution path.
- **Ban all Windows-related data or remove every compatibility branch now.**
  Rejected because a host support policy is not a data restriction, and deleting
  working code requires its own justification and regression checks.

## Consequences

Designs can stay focused on the target hosts. Native Windows users have no
installation or runtime guarantee, and Windows-specific work remains deferred
without a promised release date. Existing compatibility code may remain; this
ADR does not require an operating-system rejection check or authorize broad
cleanup. macOS/Linux targeting does not promise every distribution, architecture
or optional integration works; their existing requirements still apply.

## When to revisit

Revisit when a concrete user or deployment requirement needs native Windows and
there is ownership for its implementation, CI coverage and ongoing maintenance.
Define the supported workflows and their end-to-end verification in a new ADR
before expanding the support promise.
