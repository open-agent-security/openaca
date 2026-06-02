---
id: 0027
title: Match OSV.dev using ecosystem-specific query shapes
status: accepted
date: 2026-06-02
supersedes: null
superseded-by: null
---

## Context

PR #96 added source identities for MCP servers launched from GitHub and
Docker. The first implementation reused `ComponentRef.purl` for OSV.dev
federation, which made `pkg:github/...` and `pkg:docker/...` look queryable.
Inspecting the local `osv.dev` checkout showed that OSV.dev's PURL parser does
not map generic GitHub or Docker PURLs. Git records use `commit` queries or
`package.ecosystem = GIT` version queries, while generic Docker images do not
have a public OSV query shape equivalent to npm/PyPI package PURLs.

## Decision

OpenACA separates internal source identity from the OSV.dev query shape. npm
and PyPI refs continue to use PURL queries. GitHub refs use OSV's GIT query
semantics: immutable commit SHAs query the `commit` field, and mutable Git refs
preserved from the install source query `package: { ecosystem: "GIT",
name: "https://github.com/<owner>/<repo>.git" }, version: "<ref>"`. The
full repo-URL `.git` form is what the OSV v1 query docs specify for GIT tag
queries (`{"ecosystem": "GIT", "name": "https://github.com/curl/curl.git"}`);
OpenACA sends that documented form rather than relying on OSV's server-side
package-name normalization of a bare `github.com/<owner>/<repo>`. The bare form
is kept only as the internal repo key for stamping and record matching, where
scheme and `.git` are normalized away. Generic Docker image refs remain
inventory/BOM identities but are not sent to OSV.dev unless a later
ecosystem-specific mapping is added.

Both commit and tag/version findings trust runtime OSV query provenance.
OpenACA does not locally evaluate GIT ranges or resolve tags in V0; it treats a
commit SHA or a mutable Git tag as affected when the record was fetched through
the matching OSV query (commit or GIT version) for that repo and ref. An
explicit `affected.versions` tag match is kept as an offline fallback for the
overlay corpus, but OSV's query provenance is authoritative — OSV's server-side
GIT matching (tag resolution, range evaluation) is stronger than a local
versions-list comparison, so trusting the stamp avoids dropping records OSV
correctly matched.

## Alternatives considered

- **Use `pkg:github/...` and `pkg:docker/...` for OSV federation**: rejected
  because the inspected OSV.dev implementation does not map those PURL types
  to public query ecosystems.
- **Put Git tags back into `ComponentRef.version` and query `pkg:github`**:
  rejected because it reintroduces the ADR-0016 problem and still uses the
  wrong OSV query shape.
- **Rename OpenACA's internal `github` source ecosystem to `GIT`**: rejected
  because OSV's `GIT` is a query/range ecosystem, while OpenACA's source
  ecosystem identifies where the component came from. Keeping `github` preserves
  source-host identity and leaves room for GitLab or other Git hosts without
  collapsing them into one display namespace.
- **Implement local Git commit graph traversal immediately**: rejected for V0.
  OSV.dev already exposes commit and GIT version queries; OpenACA should use
  those before carrying a local repository-graph matcher.

## Consequences

Verbose federation output must describe OSV query targets, not just PURLs.
GitHub and Docker PURLs can still appear in BOM output as source identities,
but only supported OSV query shapes count as queryable. Git tag/branch and
commit findings both depend on OSV.dev's server-side GIT matching and the
runtime query provenance stamped on fetched records, not on a local Git range
evaluator.

## When to revisit

Revisit when OSV.dev supports generic `pkg:github` or `pkg:docker` package
queries, when OpenACA adds its own local Git range evaluator, or when container
image advisory sources provide a stable public query shape for generic Docker
images.
