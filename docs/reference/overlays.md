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

That block carries exactly four fields (the schema sets `additionalProperties: false`):

- `taxonomies` — taxonomy mappings (OWASP Agentic Top 10, OWASP MCP Top 10,
  OWASP Agentic Skills Top 10, OWASP LLM Top 10, MITRE ATLAS, and
  `supplemental_taxonomies` for anything else). CWE is not duplicated by
  default when upstream already provides it.
- `evidence_level` — confidence in the agent-context classification:
  `confirmed`, `likely`, `research`, `disputed`, or `withdrawn`.
- `threat_kind` — set to `malicious_package` for MAL-* records; omitted for
  all other record types.
- `match_coordinate` — free-text description of the specific construct the
  scanner matched on (e.g. a tool description string or config key).

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
