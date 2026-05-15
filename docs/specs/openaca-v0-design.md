# OpenACA V0 — Design Specification

*Status*: **Largely superseded** by
[ADR-0009](../adrs/0009-overlay-only-v0.md) and the current
[`openaca-thesis.md`](openaca-thesis.md).
*Last updated*: 2026-05-12.

> **Read this only as historical context.** This original V0 design described
> OpenACA as an advisory database that mints `OpenACA-YYYY-NNNN` IDs and ships
> its own `type: vulnerability | exposure | config` records. ADR-0009 retired
> that framing. V0 is now overlay-only: upstream OSV / GHSA / CVE / PYSEC / MAL
> records own vulnerability identity, affected ranges, severity, and fixes;
> OpenACA owns the `database_specific.openaca` overlay and the reference
> scanner. The thesis doc and ADR-0009 are the current sources of truth.
> Sections below remain for parser-design and identity-model context.

## 1. Overview

OpenACA (Agent Stack Vulnerabilities and Exposures) is an open, OSV-compatible
advisory database for AI agent infrastructure: plugins, MCP servers, skills,
agent frameworks, model proxies, and runtime components.

OpenACA extends the Software Composition Analysis (SCA) model to a class of
manifests that traditional SCA tooling does not yet parse — `mcp.json`,
`.claude-plugin/plugin.json`, marketplace.json, `.claude/settings.json`, and
similar agent-installation manifests. OpenACA catalogs versioned components
referenced by these manifests and adds agent-context metadata to existing
upstream advisory records.

V0 is a passive lookup layer. V1 introduces an active disclosure pipeline.

### Tagline

> Open advisories for agent stack security.

### Positioning

OpenACA is an open, OSV-compatible advisory database for vulnerabilities and
exposures in AI agent infrastructure: plugins, MCP servers, skills, agent
frameworks, model proxies, and runtime components.

## 2. Naming

| Asset | Value |
|---|---|
| Project / corpus name | OpenACA |
| Expansion | Agent Stack Vulnerabilities and Exposures |
| Repo | `open-agent-security/openaca` |
| CLI / Action | `openaca` |
| Domain | `openaca.dev` |
| Advisory IDs | `OpenACA-YYYY-NNNN` (single namespace) |
| Schema extension key | `database_specific.openaca` |

## 3. Architecture: single-namespace, type-tagged advisories

All OpenACA records share one ID space (`OpenACA-YYYY-NNNN`). Each record carries a
`type` field. The schema reserves three values; V0 ships only the first.

| `type` | Use for | V0 |
|---|---|---|
| `vulnerability` | Versioned components with a known security flaw — MCP servers, Claude Code plugins, agent builders, model proxies, packaged skills. Aliases CVE/GHSA/OSV where applicable. | ✅ Public records |
| `exposure` | Components configured in a way that creates risk without being a strict CVE-class flaw. | ⏸ Reserved in schema. **PRs proposing `type: exposure` are rejected in V0** pending an `exposure` methodology document. |
| `config` | Class-level pattern advisories (config/manifest patterns that are dangerous regardless of a specific component instance). | ⏸ V1. |

This shape mirrors CVE's "Vulnerabilities and Exposures" framing: one namespace
covering both first-class. CVE itself doesn't split into separate ID spaces;
OpenACA follows the same pattern.

### Why single-namespace beats two-corpus alternatives

- One ID format, one mental model for consumers.
- Per-type required-field enforcement happens in the linter via the `type`
  field — no namespace migration needed when adding new types.
- Consumers that read only standard OSV fields still get value from
  `type: vulnerability` records aliased to upstream IDs.

## 4. Component identity model

OpenACA advisories identify affected components in two ways. **Standard PURLs are
preferred wherever applicable**:

- `pkg:npm/<package>@<version>`
- `pkg:pypi/<package>@<version>`
- `pkg:github/<owner>/<repo>@<commit-sha>`
- `pkg:docker/<image>@<digest>`

For agent-stack registries that don't map to existing PURL ecosystems (Claude
Code plugins, Cursor extensions, MCP-stdio launches, etc.), the scanner emits
an OpenACA-native `ComponentRef.component_identity` with a registry-prefixed
scheme:

- `claude-plugin/<author>/<plugin>@<version>`
- `mcp-stdio/<package>@<version>`
- `cursor-ext/<author>/<plugin>@<version>` *(V1)*

Promoting these to standard PURL types is a future standards-proposal track,
not a V0 dependency. Canonical V0 overlays do not use native component identity
as a matching key; they enrich upstream OSV records by ID/alias.

### 4-tier detection mechanism

Component identification maps to a four-tier detection model:

| Tier | Identifier | Example | V0 status |
|---|---|---|---|
| T1 | Package + version (standard purl) | `pkg:npm/@example/server@1.2.3` | ✅ V0 |
| T2 | Manifest reference → package + version | `mcp.json` `command: "uvx pkg==1.4.0"` → `pkg:pypi/pkg@1.4.0` | ✅ V0 |
| T3 | Content hash | `sha256:...` of a skill manifest, exported flow, or template | ⏸ V1 |
| T4 | Pattern (no specific instance) | "Any `mcp.json` with unversioned `uvx`" | ⏸ V1 (`type: config`) |

V0 ships T1 + T2.

## 5. Schema

A V0 advisory is OSV-compatible JSON with an OpenACA extension. Required-field
enforcement branches on the `type` field.

### Common (all types)

- `schema_version`: pinned to the OSV schema version OpenACA tracks.
- `id`: matches `OpenACA-YYYY-NNNN`.
- `type`: one of `vulnerability`, `exposure`, `config`. Only `vulnerability` is
  permitted in V0 PRs.
- `aliases`: array of upstream IDs (`CVE-...`, `GHSA-...`, `OSV-...`, etc.).
- `summary`, `details`, `published`, `modified`.
- `severity`: CVSS v4 base + environmental vector strings.
- `references`: array of typed URLs.
- `database_specific.openaca`: see below.

### `type: vulnerability`

Adds OSV's `affected[]` array describing affected packages and version ranges.
Use standard `package.ecosystem` + `package.name` + `package.purl` where
possible.

### `database_specific.openaca` extension fields

```jsonc
{
  "database_specific": {
    "openaca": {
      "taxonomies": {
        "owasp_agentic_top10": ["asi03", "asi05"],
        "owasp_agentic_skills_top10": []     // optional, for skill advisories
      },
      "evidence_level": "confirmed",
      "threat_kind": "malicious_package"      // optional; only for malware overlays
    }
  }
}
```

Canonical overlays stay intentionally minimal. Scanner-observed component
context, such as whether a finding came from an MCP server, command, skill, or
hook, stays in scan output. Candidate review evidence and LLM provenance stay
in `candidates/` and run artifacts, not promoted overlays.

There is no `agent_blast_radius` enum or other custom severity score. Severity
comes from CVSS v4; categorization comes from OWASP ASI.

## 6. V0 deliverables

Seven items. Anything else waits.

| # | Deliverable | Notes |
|---|---|---|
| 1 | **Schema** | `schema/openaca.schema.json` — OSV-compatible JSON Schema with `type`-branching required fields. |
| 2 | **Manifest parsers** (Python) | V0 covers `package.json`, `mcp.json`, `.claude-plugin/plugin.json`, `.claude/settings.json`. Emits standard PURLs where applicable, OpenACA-native identity otherwise. Cursor + Windsurf manifests deferred to V1 (need real fixture data first). |
| 3 | **3-5 hand-curated advisories** | Mostly aliases of existing CVE/GHSA agent-component vulns, plus ≥1 enriched record demonstrating manifest detection beyond lockfile parsing. All `type: vulnerability` in V0. |
| 4 | **Linter + CI** | Hard-fail and warning discipline per §7. |
| 5 | **Static export pipeline** | `advisories/*.yaml → JSON → all.zip → modified_id.csv → GitHub Pages docs site`. No HTTP API. |
| 6 | **Reference Action** | Lives at the repo root: `action.yml`. Invocation: `open-agent-security/openaca@v1`. Thin, local-first; consumes the static export and runs manifest parsers. |
| 7 | **Disclosure policy doc** | `docs/disclosure-policy.md`. OpenSSF baseline + OpenACA-specific defaults. **Documented in V0; not operated as an active program.** See §10. |

### Out of V0

- HTTP API (`/v1/query`, etc.).
- Benchmark harness or scanner leaderboard.
- Public detection-rule format (Sigma-equivalent).
- Multi-platform CLI binary (the Action is enough).
- Active disclosure pipeline at scale.
- `type: exposure` and `type: config` records.
- T3 (hash-based) advisories.

## 7. CI discipline

Don't let flaky external dependencies break contributor PRs.

### Hard fail (block PR merge)

- Schema validation against `schema/openaca.schema.json` per `type`.
- ID format and uniqueness within `OpenACA-YYYY-NNNN` namespace.
- Required fields present per `type`.
- CVSS string parses to a valid v4 vector (where present).
- OWASP ASI categories (`asi01`–`asi10`) are valid identifiers.
- File path / namespace consistency (`advisories/YYYY/OpenACA-YYYY-NNNN.yaml`).
- Internal cross-references resolve.

### Warning or scheduled job (don't block PRs)

- Link liveness checks. GitHub, NVD, vendor sites are flaky.
- OSV/GHSA enrichment via remote API.
- Remote alias resolution (CVE → MITRE).
- Cross-corpus duplicate detection.

A pre-commit linter handles fast local checks; a nightly scheduled job handles
enrichment and link rot.

### ID reservation

Simple PR-based reservation by `tools/reserve-id.py`, which scans
`advisories/*` for the next free number. No embargo state machines, no
CNA-equivalent infrastructure in V0.

### Per-advisory evidence

Each advisory ships with minimal reproducible evidence where possible:
vulnerable config snippet, malicious tool description, unsafe plugin manifest,
affected command pattern. Treat fixtures as advisory metadata, not as a
separate corpus or pillar.

## 8. Aliasing and ID policy

Two distinct cases:

- **Records aliasing existing CVE/GHSA/OSV**: OpenACA creates the alias and
  overlays agent-context metadata. No new upstream filing required — the
  upstream record already exists.
- **OpenACA-original component vulnerabilities**: OpenACA attempts upstream
  disclosure to CVE/GHSA where appropriate, then aliases the resulting
  upstream ID. Where the affected ecosystem isn't cleanly accepted by upstream
  pipelines (some plugin marketplaces), OpenACA may carry the authoritative
  record itself. Treat this as a known gap, not a hard requirement.

OpenACA-native authority concentrates on:

- Plugin/marketplace identifiers that upstream pipelines don't currently
  cover.
- `type: exposure` records (V1 — overlay metadata + agent-context).
- `type: config` records (V1 — patterns with no specific instance).

### Enriched aliasing pattern

At least one V0 advisory should be an *enriched* record demonstrating that
OpenACA catches what lockfile-only SCA misses. Same upstream CVE/GHSA ID, but OpenACA:

- Adds reviewed taxonomy mappings and evidence level to the upstream record.
- The reference Action detects it from `mcp.json` /
  `.claude-plugin/plugin.json` / `.claude/settings.json`, not from
  `package.json`.

This makes the manifest-extension capability concrete in V0 even if all
advisories are alias-first.

## 9. Disclosure policy framework

V0 documents the policy. V0 does not operate it at scale.

OpenACA adopts the OpenSSF coordinated disclosure guidance with project-specific
defaults:

- **Default embargo**: 90 days.
- **Maintainer response checkpoint**: 21 days.
- **Nonresponsive publication review**: 35 days.
- **Active exploitation**: accelerated, case-by-case.
- **Dispute lifecycle**: `published` → `disputed` → `modified | upheld | withdrawn`.

The full policy lives at `docs/disclosure-policy.md`.

## 10. V0 → V1 gate

The gate is **readiness-based**, not time-based, and split into two
thresholds. V0 cannot complete a real coordinated disclosure end-to-end
without first having an active disclosure pipeline, so the gate separates
*starting* the pipeline from *scaling* it.

### V1 entry — start the active disclosure pipeline

ALL of:

1. **Lookup layer proof**:
   - 25+ published advisories.
   - At least 3 component types represented.
   - Static export and reference Action working from a clean install.

2. **Disclosure framework documented**:
   - OpenSSF baseline + OpenACA-specific defaults captured in
     `docs/disclosure-policy.md` with concrete process steps.

3. **Tabletop rehearsal completed**:
   - Paper exercise walking one hypothetical advisory through the full
     disclosure lifecycle (intake, validation, embargo, fix coordination,
     publication, dispute path). Identifies process gaps before a real
     maintainer is involved.

4. **Ecosystem engagement**:
   - 5+ external signals: maintainer replies, scanner-author feedback,
     GitHub stars from relevant maintainers, issue/PR contributions, one
     marketplace-operator acknowledgement, one security researcher willing
     to submit. Any combination.

### Scaling active disclosure

V1 entry conditions plus:

5. **One successful real coordinated disclosure** completed end-to-end. Real
   maintainer contact, real embargo, real fix coordination, real publication,
   real (or formally documented) dispute path. The first real case proves the
   framework before it is applied at scale.

## 11. Decisions

| Question | Decision |
|---|---|
| Project name | **OpenACA** (Agent Stack Vulnerabilities and Exposures) |
| Code license | **Apache-2.0** |
| Data license | **CC-BY-4.0** (matches OSV.dev) |
| Schema extension key | `database_specific.openaca` from day 1 |
| Canonical overlay fields | Minimal: taxonomies, evidence level, optional threat kind |
| Severity | CVSS v4 base + environmental |
| Category taxonomy | OWASP Agentic Top 10 (`asi01`–`asi10`) |
| Custom severity enum | None (no `agent_blast_radius` etc.) |
| Scanner stack | Python (Pydantic + Click); Action runs Python |
| API in V0 | No — static export only |
| VEX/SBOM in V0 | No |
| Initial corpus | 3-5 V0 advisories: mostly CVE/GHSA aliases with agent-context overlay; ≥1 enriched record demonstrating manifest detection; 0 pre-coordinated original disclosures as launch blockers; all `type: vulnerability` |
| Upstream submission | OpenACA aliases existing CVE/GHSA/OSV records. For OpenACA-original vulnerabilities, attempt upstream disclosure where the ecosystem accepts it. OSV propagation is best-effort. |
| GitHub Action layout | `action.yml` at the repo root; invocation `open-agent-security/openaca@v1` |
| `type: exposure` / `type: config` in V0 | Reserved in schema; PRs rejected pending methodology docs |
| Active disclosure in V0 | Documented only; not operated at scale |

## 12. V0 build sequence

Gates, not weeks. Ship when each gate passes.

| Phase | Gate | Deliverable |
|---|---|---|
| 0 | Repo + decision docs in place | This spec, `CLAUDE.md`, ADRs for license, extension key, naming, single-namespace architecture |
| 1 | Schema validates 5 hand-written advisories | `schema/openaca.schema.json` + `tools/lint.py`; 3-5 `type: vulnerability` advisories (mostly aliases, ≥1 enriched manifest-detection record) |
| 2 | First batch of advisories merged | Aggregator script `tools/import-from-osv.py` for the alias workflow; advisories alias real CVEs; ≥1 record demonstrates `mcp.json` / `.claude-plugin/plugin.json` detection beyond lockfile |
| 3 | Static export builds and round-trips | `uv run openaca export` produces `all.zip`, `modified_id.csv`, advisory pages on GitHub Pages |
| 4 | Reference Action detects ≥3 patterns end-to-end | `action.yml` at repo root consumes `all.zip`; manifest parsers cover `package.json`, `mcp.json`, `.claude-plugin/plugin.json`, `.claude/settings.json` (Cursor + Windsurf in V1); SARIF output; GitHub annotations |
| 5 | Disclosure policy doc published | `docs/disclosure-policy.md` complete; OpenSSF baseline + OpenACA defaults |
| 6 | Contributor guide, public launch | `README.md`, `CONTRIBUTING.md`, schema docs, "how to file an advisory" with worked examples; launch post |

## 13. Glossary

- **Advisory**: a single record in the OpenACA database, identified by an
  `OpenACA-YYYY-NNNN` ID, describing a vulnerability, exposure, or config issue
  in an agent-stack component.
- **Component**: a piece of agent infrastructure that can be installed,
  configured, or referenced — e.g., an MCP server, a Claude Code plugin, an
  agent skill, a model proxy.
- **Manifest**: a file that declares installed or referenced components.
  Examples: `package.json`, `mcp.json`, `.claude-plugin/plugin.json`,
  `.claude/settings.json`.
- **Component identity**: a stable scanner-side string identifying a
  non-package component where standard PURLs do not apply. It lives on
  `ComponentRef`, not canonical overlays.
- **Enriched record**: an advisory that aliases an existing upstream ID and
  adds OpenACA-specific metadata that makes it detectable through agent-stack
  manifests (not just lockfiles).
- **Type**: the `type` field on an advisory — `vulnerability`, `exposure`, or
  `config`. Only `vulnerability` is permitted in V0 PRs.
