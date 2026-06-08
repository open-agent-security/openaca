---
id: 0029
title: Make openaca identity the agent graph occurrence key
status: accepted
date: 2026-06-07
supersedes: 0020
superseded-by: null
---

## Context

OpenACA output currently carries several identity-like fields:
`ComponentRef.component_identity`, CycloneDX `bom-ref`, `openaca:identity`,
package PURLs, Fleet component identity, and posture
`component_identity`. These fields drifted into overlapping meanings. A
package-backed MCP server could have a PURL but no `openaca:identity`, while
the posture finding for the same configured server emitted only the local MCP
server name. A remote MCP could use `mcp-remote/<host-path>` in the BOM while a
posture finding used `mcp-server/<local-name>`.

That split is enough for local display, but it breaks consumers that need to
join posture findings, drift, policy, and inventory rows. It also makes
identity hard to explain: PURL already identifies the external package release
for OSV matching, while `openaca:identity` should answer a different question:
"where does this component live in the observed agent graph?"

ADR-0016 separated component identity, source identity, and scan context. This
ADR tightens that model by assigning each persisted field exactly one job.
It supersedes ADR-0020's remote-MCP-specific `mcp-remote/<host-path>` identity
namespace. Remote endpoint URL remains provenance and source metadata; it is
not the agent-graph occurrence key.

## Decision

`openaca:identity` is the canonical agent graph path for a component occurrence
in the observed agent surface. It is graph-shaped but scanner-normalized: it
uses stable agent-surface namespaces and parent/child occurrence names, not
filesystem paths, manifest paths, URLs, or every intermediate parser edge. It
is not a package coordinate, not a display label, and not a versioned external
source identifier.

The identity model is:

- `openaca:identity`: canonical graph path in the observed agent surface, such as
  `mcp-server/playwright`,
  `plugin/claude-plugins-official/github`, or
  `plugin/claude-plugins-official/discord/deps/npm/hono`.
- `openaca:agent_host`: agent host surface that loads, exposes, or executes
  the component, such as `claude-code`, `claude-desktop`, `cursor`,
  `windsurf`, or `vscode`. It is provenance/execution context, not a package
  ecosystem and not part of `openaca:identity`.
- `purl`: external package/source coordinate used for OSV-compatible matching,
  such as `pkg:npm/%40playwright/mcp@latest` or `pkg:npm/hono@4.12.5`.
- `version`: observed installed/source version. Versions stay outside
  `openaca:identity` unless the component's agent-graph occurrence name is
  inherently versioned.
- `bom-ref`: local CycloneDX row reference. OpenACA-generated BOMs prefer the
  `openaca:identity` value and add a deterministic suffix only when duplicate
  occurrence keys appear in one BOM.
- `name`: display label only. It can be short and user-friendly, but it is not
  a join key.
- `source_manifest`, `source_locator`, `openaca:url`,
  `openaca:install_source`, and `component_path`: provenance and context. They
  explain how the occurrence was observed, but they are not substitutes for
  `openaca:identity`.

OpenACA-generated BOM components MUST carry `openaca:identity`, including
package-backed components. Package-backed MCP servers use the configured MCP
server occurrence identity (`mcp-server/<server-name>`) and keep their package
PURL separately. Plugin-bundled package dependencies use the plugin occurrence
as their parent and add an unversioned dependency path:

```text
openaca:identity   = plugin/claude-plugins-official/discord/deps/npm/hono
openaca:agent_host = claude-code
purl               = pkg:npm/hono@4.12.5
version            = 4.12.5
```

Posture findings MUST reference the same `openaca:identity` as the BOM
component they describe. Posture rules should emit the canonical identity
directly where possible; upload-time alignment remains a defensive fallback,
not the primary identity mechanism.

Advisory matching continues to use source identity (`purl`, Git query
provenance, or explicit OpenACA component identity targets for source-less
components). A finding row may still attach to a CycloneDX `bom-ref` when a
specific BOM row is needed, because a single agent-graph occurrence identity
can appear more than once in unusual BOMs.

## Alternatives considered

- **Use PURL as the component identity whenever available.** Rejected because
  PURL identifies the external package release, not the configured agent
  surface. It cannot explain that `@playwright/mcp` is installed as the
  `playwright` MCP server, and it gives posture findings no stable key when
  the posture concern is the server configuration rather than the package.
- **Keep remote MCP URL identities (`mcp-remote/<host-path>`).** Rejected
  because URL is source/provenance metadata. It does not match the local agent
  graph occurrence that posture rules and users see, and it made remote MCP
  posture findings join by a different key than the BOM component.
- **Include package versions in `openaca:identity`.** Rejected because version
  changes would look like component replacement rather than component drift.
  Version belongs in the component version and PURL fields.
- **Let downstream consumers infer identity from name, PURL, or paths.**
  Rejected because each consumer would invent slightly different fallback
  rules. OpenACA owns the scanner semantics and should emit the join key
  explicitly.
- **Make source path the primary occurrence key.** Rejected because local paths
  are private, unstable across machines, and already handled as provenance.
  Path-derived suffixes are acceptable only as deterministic collision
  disambiguators.
- **Include the agent host in `openaca:identity`.** Rejected because a host
  value such as `claude-code` is provenance/execution context, while the graph
  path should stay stable across equivalent host-specific observations. Host
  context belongs in `openaca:agent_host`.

## Consequences

- BOM, posture, policy, drift, and export consumers can join on one
  scanner-owned occurrence key instead of mixing package, URL, display, and
  path identities.
- OSV matching remains interoperable because PURL and Git source metadata keep
  their existing meaning.
- Remote MCP URL identity records generated before this ADR may not compare
  cleanly with newly generated `mcp-server/<name>` identities. This is an
  acceptable pre-V0 compatibility cost.
- OpenACA's BOM linter can require `openaca:identity` on every generated
  component rather than accepting PURL-only package components.
- Some rare duplicate occurrences may need deterministic `bom-ref` suffixes
  even when they share `openaca:identity`; row-specific advisory attachment
  should use `bom-ref` when row identity matters.

## When to revisit

Revisit if the MCP ecosystem publishes a canonical server identity that
captures both configured occurrence and source endpoint/package identity better
than the current split. Revisit if duplicate occurrence keys become common
enough that OpenACA needs a first-class `openaca:occurrence_key` separate from
human-readable `openaca:identity`.
