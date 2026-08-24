---
id: 0048
title: Compile source-based policy restrictions
status: accepted
date: 2026-08-23
supersedes: null
superseded-by: null
---

## Context

Agent scans produce observations: exact component occurrences use `bom-ref`,
and some components also have the optional source-stable `openaca:identity`
(ADR-0042). Both are useful for explaining a scan, but neither is a suitable
administrator-facing policy target. A BOM reference changes with the scanned
composition; identity can be absent; and neither necessarily names a value that
an agent host can enforce.

The policy engine also needs a hard safety boundary. Managed host configuration
may merge user, project, and organization settings. An OpenACA policy that
emits an allow decision can accidentally widen access instead of applying an
organization restriction.

## Decision

OpenACA policy uses exact host-recognizable source/configuration values as its
targets: an MCP command array or URL, a plugin identifier, or a marketplace
source. It does not target a BOM reference, `openaca:identity`, a digest, a
display name, or a broad pattern. BOM occurrences, identities, graph
containment, advisories, and posture findings remain evaluation evidence.

OpenACA policy compilation only adds restrictions. Admission has explicit
allowed and blocked source lists plus a category default; risk gates turn an
otherwise admitted component into blocked. Compilation never emits an allow
decision intended to grant a host privilege. A host compiler must report a
matching result as not enforceable when it cannot produce an exact native
restriction.

## Alternatives considered

- **Attach policies to `bom-ref` or `openaca:identity`.** Rejected because
  `bom-ref` is scan-local, identity can be absent, and neither is necessarily a
  host-native enforcement coordinate. This would make policy follow inventory
  implementation details rather than administrator intent.
- **Use a generic selector language over all BOM fields.** Rejected because
  globs, regular expressions, and composite predicates are hard to explain,
  easy to overmatch, and cannot reliably compile to host settings. The first
  policy format has a small fixed vocabulary of exact source targets and risk
  gates.
- **Treat policy allowlists as host grants.** Rejected because host settings can
  merge across scopes. An OpenACA-issued allow could widen a user’s available
  access. OpenACA may express its own default and restrictions, but must not
  claim that an allow grants more than the host already permits.
- **Create an OpenACA runtime proxy or hook for every decision.** Rejected for
  the initial design because it adds a separate execution boundary and does not
  make unsupported host surfaces enforceable. Native managed configuration is
  the initial compiler target.

## Consequences

The policy model is small enough to write without an inventory UI and stable
enough to use across scans. The compiler can use rich OpenACA evidence without
turning that evidence model into policy syntax. Plugin containment remains
meaningful for risk attribution: a finding in bundled content blocks the owning
plugin rather than inventing a child-level exception.

The tradeoff is intentionally limited expressiveness. Policy cannot name every
observed artifact, individual skills installed outside plugins may only receive
a category-wide restriction, and a component with no host-native coordinate can be reported but
not blocked by the compiler. More expressive selectors or runtime enforcement
need a new decision with clear host semantics.

## When to revisit

Revisit when a supported host exposes a stable, managed enforcement coordinate
that the current policy document cannot name, or when real policy users show a
specific need that cannot be expressed with exact source targets and risk
gates. Do not revisit merely because a BOM contains a convenient identifier;
that would conflate scan evidence with administrator intent again.
