# OpenACA Thesis

> Companion to [`openaca-v0-design.md`](openaca-v0-design.md). The design doc
> says **what** V0 ships. This doc says **why** the project exists and **what
> it contributes** to the agent-stack security ecosystem.

## What OpenACA is

**OpenACA — Open Agent Composition Analysis.** The open category and reference
implementation for *Agent Composition Analysis (ACA)*: identifying the
versioned plugins, MCP servers, skills, and agent-framework components an
AI agent stack is composed of, and matching them against known security
records.

ACA is the agent-stack analogue of Software Composition Analysis (SCA): SCA
inventories your library tree from `package.json` / `requirements.txt`; ACA
inventories your agent stack from `mcp.json`, `.claude-plugin/plugin.json`,
`.claude/settings.json`, and similar agent-installation manifests. The two
layers stack — they answer different questions about different artifacts.

## Tagline

> **Open agent-context overlays for upstream security advisories.**

## One-paragraph thesis

OpenACA is the open, OSV-compatible overlay layer for Agent Composition
Analysis: it resolves agent-installation manifests to component identities,
matches them against upstream records (GHSA / CVE / OSV / PYSEC / MAL), and
adds agent-context metadata for triage. OpenACA does not mint vulnerability
IDs; upstream sources own identity, affected ranges, severity, and fixes.
OpenACA owns the overlay (`database_specific.openaca`) and the manifest
parsers that traditional SCA tooling doesn't cover.

## What's already covered

1. **Versioned-package advisories on npm/PyPI are already covered by GHSA.**
   When an MCP server is published as `@modelcontextprotocol/server-X` on npm
   or `mcp-server-Y` on PyPI, recent CVEs flow through GHSA → OSV → Dependabot.
   OpenACA does not replace this pipeline. It enriches it.
2. **Agent-stack scanning is being addressed by artifact-analysis tools.**
   Several Apache-2.0 / open scanners exist that inspect MCP servers, plugin
   manifests, IDE config, and skill content for unsafe patterns. They operate
   primarily as artifact analysis tools (SAST-shaped). Their output is
   pattern-match findings, not lookups against a database.
3. **OWASP Agentic Top 10 has settled the taxonomy.** Categories ASI01–ASI10
   are the standard reference. OpenACA consumes this taxonomy; it does not
   redefine it.

## What's not already covered

1. **Plugin / marketplace component records with stable versioned
   identifiers.** Claude Code plugins (`.claude-plugin/plugin.json` carries an
   explicit semver `version`), Cursor extensions, Windsurf plugins, and
   marketplace.json registries all have versioned components, none are parsed
   by SCA tools today, and none have agent-context metadata layered on top of
   the upstream records that *do* exist. This is the primary gap.
2. **Manifest-based component installations that bypass the lockfile.** MCP
   servers installed via `mcp.json` `command: "uvx pkg==1.4.0"` or `npx pkg`
   *can* have known CVEs against the underlying package, but standard SCA
   tooling misses them because the package never enters `package.json` /
   `requirements.txt`.
3. **A vendor-neutral, mirrorable, openly licensed agent-context corpus**
   that users can rely on without depending on any single scanner
   vendor's data pipeline.

OpenACA addresses those three gaps: **the open, OSV-compatible
agent-context overlay corpus that extends SCA to plugin / MCP / marketplace
manifests, with OWASP ASI category mapping and evidence-grade triage
metadata.**

## SAST, SCA, and ACA — layered

The agent-stack security stack has three complementary layers, mirroring the
SAST/SCA split familiar from traditional software security.

| Layer | What it does | Artifact | Examples |
|---|---|---|---|
| **SAST** | Inspects an artifact for unsafe patterns | Source code, configs, manifests | Pattern-checking scanners for MCP servers, plugin manifests, skill content, IDE configs |
| **SCA** (Software Composition Analysis) | Identifies third-party library components by version, looks them up against a vuln DB | `package.json`, `requirements.txt`, lockfiles | OSV-Scanner, Trivy, Dependabot |
| **ACA** (Agent Composition Analysis) | Identifies third-party agent-stack components by version, looks them up against a vuln DB | `mcp.json`, `.claude-plugin/plugin.json`, `.claude/settings.json`, marketplace registries | **OpenACA + upstream OSV** |

These layers stack. They do not compete. Mature security tooling runs all
three because they catch different signal types.

What composition-analysis (SCA / ACA) gets you that pure artifact analysis
cannot:

1. **Disclosure-time alerting.** A vuln is disclosed today; tomorrow your
   manifest survey across N projects flags every install. Pure analysis would
   have to re-find the issue on each scan.
2. **Fix-version tracking.** Upstream records carry `affected[]` ranges and
   fix versions; users can auto-PR the bump. Analysis flags the pattern
   but rarely tells you the path to fixed.
3. **Speed at fleet scale.** Lookup is O(N) hash-against-DB. Analysis is O(N
   × analysis-time). For an org with many agent installations, the difference
   matters.
4. **Cross-component fleet view.** *"Across our org, what is our exposure to
   GHSA-3q26-f695-pp76?"* — answerable from a manifest survey + DB lookup.
5. **Pre-install gates.** CI workflow can ask *"is this manifest about to
   install a known-vulnerable component?"* before the install lands.
6. **Audit trail.** *"Were we exposed to CVE-2026-20205 between March and
   May?"* — a DB + lockfile history answers it.
7. **Vendor-neutral baseline.** Composition-analysis decisions reduce to
   *"version X has vuln Y"* — same answer regardless of scanner. The DB is
   the shared substrate everyone agrees on.

Plugins, MCP servers, and skills are typically *not your code*. The most
actionable signal isn't *"this third-party thing has unsafe patterns"* — it's
*"this third-party thing has a publicly disclosed vuln with a fix version."*
That is a composition-analysis finding by definition. Agent-installation
manifests (`mcp.json`, `.claude-plugin/plugin.json`) are lockfile-equivalent:
they carry version semantics. The substrate ACA needs already exists in the
format. OpenACA is the agent-context overlay that closes the loop.

## What OpenACA adds beyond upstream OSV

OpenACA does not mint its own vulnerability IDs. Overlays are keyed by
upstream IDs (GHSA-*, CVE-*, OSV-*, PYSEC-*, MAL-*) and live at
`overlays/<upstream-id>.yaml`. See
[ADR-0009](../adrs/0009-overlay-only-v0.md).

Three things OpenACA overlays contain that a generic OSV/GHSA record does not:

1. **`taxonomies{}`** — mapping of OpenACA-owned agent-context taxonomy
   families, including `owasp_agentic_top10[]` entries referencing
   ASI01–ASI10 categories. Lets users triage findings by the framework
   category instead of just CVE list.
2. **`evidence_level`** — enum `confirmed | likely | research | disputed |
   withdrawn`. Lets users filter noise. Auto-fix on confirmed,
   ticket-only on research-grade.
3. **`threat_kind`** — a narrow OpenACA-owned classification for records
   where upstream OSV shape is too generic. V0 only allows
   `malicious_package`, and only on MAL-* records.

Scanner output still carries observed component context such as package
PURLs, MCP launch declarations, plugin attribution, and non-PURL
`ComponentRef` identity. That context is deliberately not duplicated into
canonical overlays.

These fields all live under `database_specific.openaca` in the OSV schema.
OSV reserves that namespace exactly for per-database extensions.
OpenACA-aware tooling reads the overlay; OSV-compliant generic tooling
ignores it. **Backwards-compatible by design.**

## What OpenACA contributes

Two concrete contributions OpenACA brings to the agent-stack security stack
that don't exist elsewhere today:

1. **The agent-context overlay schema.** A namespace
   (`database_specific.openaca`) for agent-stack taxonomy mappings, evidence
   level, and a narrow malicious-package threat kind, layered on top of
   upstream OSV records. The schema is the durable contribution: even when
   adjacent ecosystems incorporate similar metadata, they will use OpenACA's
   namespace and definitions.
2. **Manifest parsers and install-time component identity.** PURL is a
   strong substrate for components distributed via npm, PyPI, GitHub
   releases, Docker. It doesn't (today) reach Claude Code plugins
   distributed via marketplace.json indirection, Cursor extensions, Windsurf
   plugins, or stdio-launched MCP servers identified by command + args.
   OpenACA's parsers and `ComponentRef` identity model follow install-time
   identity, not source-tree identity. The longer-term direction is to
   contribute these patterns back to standards bodies (PURL types, OSV
   schema), with OpenACA as the working reference.

Both are explicitly *additive* to OSV / GHSA / Dependabot. The substrate
those projects steward — vulnerability identity, affected ranges, severity,
fixes — is upstream of OpenACA. OpenACA reads from them and contributes back
the agent-stack layer.

## What makes OpenACA useful

A few practical reasons users reach for OpenACA today, even though the
underlying upstream records are public:

- **Domain focus.** OpenACA is agent-infrastructure-specific. A user
  asking *"what's my agent-tool-hijack exposure?"* gets a clean answer from
  OpenACA's taxonomy; from a generic CVE feed they have to reconstruct it.
- **Manifest coverage.** OpenACA's parsers read agent-installation
  manifests (`mcp.json`, `.claude-plugin/plugin.json`, etc.) that
  general-purpose SCA tooling doesn't parse today.
- **Iteration speed.** A focused project iterating on agent-stack overlays
  ships faster than waiting on agent-context fields to land in a
  general-purpose security database.
- **Standards path.** The schema namespace and identity conventions are
  candidate inputs to broader standardization (PURL, OSV). OpenACA is the
  open implementation those proposals reference.

## Why OSS

OpenACA is open source because that's how a shared security substrate
actually works.

1. **Friction-free ingestion.** Scanners, aggregators, downstream tooling,
   and individual researchers should be able to pull the corpus without
   asking anyone's permission. CC-BY-4.0 (matching OSV.dev) is the
   data-license norm; anything more restrictive blocks the project from
   doing its job.
2. **Neutrality.** Vuln databases work because users trust them as
   neutral. An open corpus stays neutral; a closed one looks like any
   single vendor's private feed.
3. **Shared schema.** The agent-context overlay is most useful when it's a
   convention everyone reads and writes the same way. Shared schemas spread
   from open reference implementations.
4. **Contribution flow.** Security researchers, individual maintainers, and
   vendor security teams contribute to open vuln databases (GHSA, OSV)
   regularly; closed ones rarely see that kind of contribution. Open is
   simply the format the community already operates in.

License decisions:

- **Source code: Apache-2.0.** Patent grant matters for a schema-and-tooling
  project that may be incorporated into larger systems. See
  [ADR-0001](../adrs/0001-licenses.md).
- **Overlay data: CC-BY-4.0.** Matches OSV.dev; avoids share-alike viral
  terms blocking mixed-license downstream users. See
  [ADR-0001](../adrs/0001-licenses.md).

## V0 → V1 expansion path

| Phase | Scope |
|---|---|
| **V0** | Overlay-only. Each canonical record sits at `overlays/<upstream-id>.yaml`, keyed by an upstream OSV record ID (GHSA / CVE / OSV / PYSEC / MAL), and contains only `database_specific.openaca` metadata: taxonomies, evidence level, and (for malicious packages) threat_kind. Schema, linter, static export, reference scanner (CLI + Action). Disclosure policy doc; private-pilot operation only. See [`openaca-v0-design.md`](openaca-v0-design.md) for the canonical V0 deliverable list. |
| **V1 entry** | Active disclosure pipeline. Triggered when V0 ships ≥ 25 overlays across 3+ component types, the disclosure framework is documented + tabletop-rehearsed, and ≥ 5 external ecosystem signals materialize. |
| **V1 scaling** | Programmatic disclosure sweeps; first real coordinated disclosure completed end-to-end. |
| **V1 manifest coverage** | Cursor + Windsurf + ChatGPT-style plugin manifests join the V0 set (`package.json`, `mcp.json`, `.claude-plugin/plugin.json`, `.claude/settings.json`). |
| **V1 hash-based identity** | Content-hash-keyed overlays for skill manifests, exported flows, and copy-pasted IDE rule templates. |

V0 is the credibility floor. V1 is when OpenACA becomes the corpus
users expect to query, not just an archive of overlays on existing
records.

## References

- OSV schema: <https://github.com/ossf/osv-schema>
- OWASP Agentic Top 10 (2026 edition):
  <https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/>
- OWASP Agentic Skills Top 10:
  <https://owasp.org/www-project-agentic-skills-top-10/>
- Claude Code plugin manifest schema:
  <https://code.claude.com/docs/en/plugins-reference>
- MCP protocol & ecosystem (Linux Foundation hosted):
  <https://modelcontextprotocol.io/>
- OpenACA V0 design: [`openaca-v0-design.md`](openaca-v0-design.md)
- ADR-0001 — Licenses: [`../adrs/0001-licenses.md`](../adrs/0001-licenses.md)
- ADR-0002 — Schema extension key:
  [`../adrs/0002-schema-extension-key.md`](../adrs/0002-schema-extension-key.md)
- ADR-0009 — Overlay-only V0:
  [`../adrs/0009-overlay-only-v0.md`](../adrs/0009-overlay-only-v0.md)
