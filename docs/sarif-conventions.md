# ASVE SARIF Conventions

ASVE emits SARIF v2.1.0 with ASVE-specific extensions under
`runs[].results[].properties`. This document is the contract for
downstream consumers.

## Property reference

| Key | Type | Values | Set when |
|---|---|---|---|
| `attributed_to` | string \| absent | `"claude-plugin/<name>@<version>"` | The component was discovered via an active plugin's installPath (ADR-0006). Absent when the component is direct (bare in settings, repo-declared, host repo lockfile). |
| `coverage` | string \| absent | `"transitive"` \| `"direct-only"` | Tier-2 implementation-dep findings. `"transitive"` when the ref came from a lockfile; `"direct-only"` when it came from a manifest fallback (no lockfile for that ecosystem). Absent on Tier-1 inventory findings (ADR-0007, ADR-0008). |
| `transitive` | bool \| absent | `true` \| `false` | Bool mirror of `coverage` for easier downstream parsing. Absent when `coverage` is absent. |
| `source` | string \| absent | `"asve.dev"` \| `"osv.dev"` | The advisory record's provenance. `"asve.dev"` is the local ASVE corpus; `"osv.dev"` is OSV.dev (when `--federate-osv` was set during the scan). Absent when no source is declared on the advisory. |

## Stability promise

Per ADR-0008 these properties are part of the V0 contract. Adding new
properties or new values to existing properties is non-breaking; removing
or changing semantics requires a superseding ADR.

## Example

```json
{
  "ruleId": "GHSA-FAKE-LODASH",
  "level": "error",
  "message": { "text": "lodash@4.17.20 matches GHSA-FAKE-LODASH" },
  "locations": [/* ... */],
  "properties": {
    "attributed_to": "claude-plugin/superpowers@5.1.0",
    "coverage": "transitive",
    "transitive": true,
    "source": "osv.dev"
  }
}
```

This result means: `lodash@4.17.20` was found in the transitive tree
(`coverage=transitive`) of the active plugin `superpowers@5.1.0`
(`attributed_to`); the advisory came from OSV.dev's federation pass
(`source=osv.dev`).

## Why these keys

`attributed_to` answers "which plugin should I remediate?" — directly
actionable. `coverage`/`transitive` distinguish "lockfile says this is in
the tree" from "manifest says this is a declared direct dep, transitive
unknown." `source` lets corpus-aware consumers (e.g., users running
ASVE-only governance) filter out federation-sourced findings.
