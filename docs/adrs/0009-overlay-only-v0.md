---
id: 0009
title: V0 ships ASVE overlays, not ASVE advisory IDs
status: accepted
date: 2026-05-12
supersedes: [0003, 0004, 0008]
superseded-by: null
---

## Context

The first ASVE corpus records duplicated upstream GHSA/CVE records while
adding `database_specific.asve` agent context. Dogfooding exposed the
cost of that duplication: hand-authored severity and fixed-version data
drifted from upstream, OSV.dev can expose multiple records for one issue
(`GHSA-*` and `CVE-*`), and exact-ID dedup double-counted the same
vulnerability. V0 is pre-launch, so we can remove the duplicate
vulnerability database process before consumers depend on ASVE-issued IDs.

## Decision

ASVE V0 is an overlay corpus plus scanner. Overlay files live at
`overlays/<upstream-id>.yaml`, use the upstream OSV/GHSA/CVE identifier
as `id`, and carry only ASVE-owned agent context under
`database_specific.asve`. OSV.dev provides vulnerability identity,
affected package/version ranges, severity, fixes, summaries, and
references at scan time. The scanner queries OSV.dev by emitted PURL,
deduplicates records by alias graph, then applies ASVE overlays by
alias-set intersection. The CLI has no `--db` or `--overlays` flag in V0;
the bundled overlay corpus is implicit.

## Alternatives considered

- **Keep full ASVE advisory records aliasing upstream**: rejected because
  ASVE would still need to synchronize upstream severity/fixes, resolve
  duplicate alias records, and operate a vulnerability database process
  before V0 has the governance to do that credibly.
- **Keep ASVE IDs but strip upstream fields**: rejected because it keeps
  a parallel identity namespace without adding matching precision. The
  useful identity for aliasing and dedup is the upstream equivalence set.
- **Expose `--overlays` for custom corpora in V0**: rejected as premature
  surface area. The bundled OSS corpus is the V0 product; custom mirrors
  can be revisited after packaging and update semantics are clearer.

## Consequences

- V0 scans require OSV.dev connectivity for package vulnerability
  matching. Network failures are fail-soft, but overlays alone do not
  contain affected ranges and cannot independently prove a package version
  vulnerable.
- ASVE avoids owning severity, fixed-version, withdrawal, and dispute
  processes for upstream vulnerabilities.
- SARIF and human output use upstream IDs (`GHSA-*`, `CVE-*`) as rule IDs.
  ASVE contribution is represented as `database_specific.asve` metadata
  and SARIF `overlay_source=asve.dev`.
- ASVE-native vulnerabilities for ecosystems upstream cannot model are
  deferred. If that need becomes real, a future ADR can introduce an
  ASVE-native advisory lane with explicit governance.

## When to revisit

Revisit when ASVE needs to publish vulnerabilities that have no viable
upstream OSV/GHSA/CVE home, or when users require offline matching with a
bundled OSV mirror.
