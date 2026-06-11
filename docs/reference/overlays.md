# Overlay Reference

OpenACA overlays add agent-specific context to upstream advisory records. They
do not replace CVE, GHSA, OSV, PYSEC, or MAL records, and OpenACA does not mint
its own vulnerability IDs.

## ID format

Overlay files use the upstream advisory ID, usually the OSV record ID:

```text
GHSA-...
CVE-...
PYSEC-...
MAL-...
```

Overlay files live under `overlays/` and are named `<upstream-id>.yaml`.

## Aliases

Overlays list known equivalent IDs so they can merge with any OSV record whose
alias set intersects. This lets OpenACA enrich a finding whether OSV returns
the GHSA, CVE, PYSEC, or other equivalent identifier.

## Severity and fixes

Severity, affected ranges, fixed versions, references, and CVSS vectors come
from upstream OSV / GHSA / CVE records. OpenACA overlays do not duplicate or
override upstream ownership of those fields.

## Agent context

OpenACA-specific context lives under `database_specific.openaca`.

That block carries fields such as:

- `component_type`
- `surfaces`
- `agent_impact`
- evidence metadata
- taxonomy mappings

Taxonomies include OpenACA-defined mappings such as OWASP Agentic Top 10 and
OWASP MCP Top 10. CWE is not duplicated by default when upstream already
provides it.

## Local scan context stays out of overlays

Overlay records are advisory data. They do not store local scan context such
as:

- which plugin introduced a dependency;
- whether a component was observed in repo mode or endpoint mode;
- a local file path;
- an Agent BOM component path.

That context belongs in scan output and Agent BOMs, not advisory overlays.

## Source of truth

- Sample overlay:
  [`overlays/GHSA-3q26-f695-pp76.yaml`](../../overlays/GHSA-3q26-f695-pp76.yaml)
- Schema:
  [`schema/openaca.schema.json`](../../schema/openaca.schema.json)
- Overlay authoring:
  [`CONTRIBUTING.md`](../../CONTRIBUTING.md)
