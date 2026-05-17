---
id: 0016
title: Separate agent component source identity from scan context in output
status: accepted
date: 2026-05-15
supersedes: null
superseded-by: null
---

## Context

OpenACA V0 keeps canonical overlays minimal. Per ADR-0012, overlays do not
carry scanner-observed fields such as component type, component identity,
surface, or local agent impact. Those facts are observations from a particular
scan, not stable advisory metadata.

At the same time, scan output needs to explain more than a package/version
match. Agent components can enter the runtime directly or through containers:
a skill can be installed directly from a GitHub repo, bundled by a plugin, or
copied locally; an MCP server can be declared directly in an MCP config or by
a plugin; a package dependency can be an implementation dependency of a plugin.
The same vulnerable source artifact should match the same advisory regardless
of how it entered the local agent stack, but users still need to know where it
was observed and what runtime can reach it.

Vercel's `skills` CLI reinforces this distinction. It installs skills into
agent-specific or universal locations, but stores provenance in lock files as
source data such as `github`, `gitlab`, `git`, `well-known`, `local`, or
`node_modules`. `skills.sh` is useful discovery/search infrastructure, but the
installed artifact's source identity is the underlying Git/repo/well-known
source, not the search site used to find it.

## Decision

OpenACA scan output separates three identity layers:

1. **Component identity**: the risky or vulnerable thing the scanner is
   reporting, such as a skill, MCP server, plugin, hook, command, agent, or
   package.
2. **Source identity**: the artifact identity used for advisory matching, such
   as an npm/PyPI/Docker package PURL or a GitHub repository plus subpath and
   revision/content hash.
3. **Scan context**: how the component entered and runs in the local agent
   stack, including `declared_by`, `component_path`, install path, and
   `active_in` runtime hosts.

Advisory matching uses source identity. Direct installation versus
plugin-bundled installation changes scan context, not the source identity being
matched. For example, a vulnerable MCP server installed directly and the same
MCP server declared by a plugin match the same advisory. The output explains
the difference with `declared_by` and `component_path`.

OpenACA uses PURL-shaped source identifiers only when they describe the actual
source ecosystem. Official package ecosystems use their official PURL types:
`pkg:npm`, `pkg:pypi`, `pkg:docker`, and `pkg:github` where GitHub repository
identity is the real source. OpenACA does not put `openaca` in the PURL type,
because OpenACA is not the package ecosystem. OpenACA also does not use
`skills.sh` as a canonical source ecosystem in V0. If `skills.sh` provenance is
observable, it may be recorded later as discovery metadata, but it is not part
of the matching key.

Mutable refs are represented explicitly. A GitHub skill installed from `main`
may carry `ref: main` and a content or tree hash, but the canonical PURL must
not pretend `@main` is an immutable version. If an immutable commit SHA is
known, it can be represented as a revision. If only a content hash is known,
the output carries that hash separately from source revision.

Scanner-emitted posture findings use the same output envelope for component,
source, and context, but remain scanner findings. They are not overlay records
and do not require changes to `schema/openaca.schema.json`. Plan 015
implements this decision for scan output.

## Alternatives considered

- **Store component identity and component type in overlays**: rejected by
  ADR-0012. Those fields describe local scan observations and can differ by
  repository, host, plugin container, or endpoint configuration.
- **Treat plugin-bundled components as different advisory identities**:
  rejected because the vulnerable source artifact is the same. Bundling affects
  reachability and remediation guidance, not the advisory match key.
- **Use `skills.sh` as a first-class ecosystem**: rejected for V0 because the
  Vercel `skills` CLI ultimately installs from GitHub, GitLab, generic git,
  well-known, local, or node_modules sources and records those sources in lock
  files. `skills.sh` is a discovery channel, not the authoritative artifact
  ecosystem for installed skills.
- **Use `pkg:openaca/...` for provisional agent components**: rejected because
  it makes OpenACA look like the artifact ecosystem and weakens interoperability
  with package-url consumers. Structured OpenACA fields can carry provisional
  metadata without overloading PURL type semantics.
- **Encode mutable refs such as `main` in PURL `@version`**: rejected because it
  makes mutable source look immutable. Mutable refs should be visible as posture
  risk and should not be normalized into stable advisory identities.

## Consequences

JSON output gains a stable envelope around findings:

- `finding_type`
- `component`
- `component.source`
- `active_in`
- `declared_by`
- `component_path`
- `matched_advisory` for vulnerability findings
- `rule_id` and `standards` for posture findings

Text output should lead with the vulnerable or risky component, then explain
source identity and scan context. SARIF should preserve the same metadata in
result properties so downstream code-scanning and dashboard integrations do not
need to infer containment from free-form messages.

Parsers need to attach enough metadata to `ComponentRef.extra` to explain
direct and bundled components, while preserving existing matching behavior.
`ComponentRef.purl` remains useful for official package ecosystems; richer
source/context output is built around it rather than replacing it.

The model keeps the overlay corpus clean and still gives users immediate
answers to "what is affected?", "where did it come from?", "how did it enter my
agent stack?", and "what runtime can reach it?"

## When to revisit

Revisit if a skills registry becomes an authoritative artifact ecosystem with
stable versions, immutable artifacts, and lock metadata that records registry
identity as the source of truth. Revisit if package-url standardizes agent
component types that should replace OpenACA's provisional structured fields.
