# OpenACA SARIF Conventions

OpenACA emits SARIF v2.1.0 with OpenACA-specific extensions under
`runs[].results[].properties`. This document is the contract for
downstream users.

## Property reference

| Key | Type | Values | Set when |
|---|---|---|---|
| `attributed_to` | string \| absent | `"claude-plugin/<name>@<version>"` | The component was discovered via an active plugin's installPath (ADR-0006). Absent for direct components declared in settings, repo manifests, or host repo lockfiles. |
| `coverage` | string \| absent | `"transitive"` \| `"direct-only"` | Tier-2 implementation-dep findings. `"transitive"` when the ref came from a lockfile; `"direct-only"` when it came from a manifest fallback (no lockfile for that ecosystem). Absent on Tier-1 inventory findings (ADR-0007, ADR-0008). |
| `transitive` | bool \| absent | `true` \| `false` | Bool mirror of `coverage` for easier downstream parsing. Absent when `coverage` is absent. |
| `source` | string \| absent | `"osv.dev"` | The matched vulnerability record's provenance. V0 package vulnerability records come from OSV.dev. |
| `overlay_source` | string \| absent | `"openaca.dev"` | The finding's upstream record matched a bundled OpenACA overlay. Absent when no OpenACA overlay applied. |

## Stability promise

Per ADR-0009 these properties are part of the V0 contract. Adding new
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
    "source": "osv.dev",
    "overlay_source": "openaca.dev"
  }
}
```

This result means: `lodash@4.17.20` was found in the transitive tree
(`coverage=transitive`) of the active plugin `superpowers@5.1.0`
(`attributed_to`); the vulnerability record came from OSV.dev
(`source=osv.dev`) and OpenACA contributed an agent-context overlay
(`overlay_source=openaca.dev`).

## Why these keys

`attributed_to` answers "which plugin should I remediate?" — directly
actionable. `coverage`/`transitive` distinguish "lockfile says this is in
the tree" from "manifest says this is a declared direct dep, transitive
unknown." `source` and `overlay_source` distinguish upstream
vulnerability data from OpenACA-owned agent context.
