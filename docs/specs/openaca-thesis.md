# OpenACA Thesis

> Companion to [`openaca-v0-design.md`](openaca-v0-design.md). The design doc says
> **what** V0 ships. This doc says **why** the project exists, **what gap** it
> fills, and **why the OSS authority position is durable** as adjacent
> ecosystems evolve.

## Tagline

> **Open agent-context overlays for upstream security advisories.**

## One-paragraph thesis

OpenACA is the open, agent-context overlay layer for AI agent infrastructure:
it resolves agent-installation manifests (`mcp.json`,
`.claude-plugin/plugin.json`, `.claude/settings.json`, and similar) to
component identities, matches them against OSV-compatible upstream records
(GHSA / CVE / OSV / PYSEC / MAL), and adds agent-context metadata for triage.
OpenACA does not mint vulnerability IDs; upstream sources own identity,
affected ranges, severity, and fixes. OpenACA owns the overlay
(`database_specific.openaca`) and the manifest parsers that traditional SCA
tooling doesn't cover.

## What's covered, what isn't

Three honest facts about what's already covered in the agent-stack security
space:

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

Three honest facts about what is *not* covered:

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
   that consumers can rely on without depending on any single scanner
   vendor's data pipeline.

OpenACA addresses those three gaps: **the open, OSV-compatible
agent-context overlay corpus that extends SCA to plugin / MCP / marketplace
manifests, with OWASP ASI category mapping and evidence-grade triage
metadata.**

## SAST and SCA, layered

The agent-stack security stack has two complementary layers, just like
traditional software security.

| Layer | What it does | Knowledge source | Examples |
|---|---|---|---|
| **SAST** (artifact analysis) | Inspects an artifact for unsafe patterns | Rules + analysis logic | Pattern-checking scanners for MCP servers, plugin manifests, skill content, IDE configs |
| **SCA** (database lookup) | Identifies third-party components by version, looks them up against a vuln DB | Database + parsers | OSV-Scanner, Trivy, Dependabot for traditional packages; **OpenACA + upstream OSV for agent-stack manifests** |

These layers stack. They do not compete. Mature security tooling runs both
because they catch different signal types.

What SCA gets you that pure artifact analysis cannot:

1. **Disclosure-time alerting.** A vuln is disclosed today; tomorrow your
   manifest survey across N projects flags every install. Pure analysis would
   have to re-find the issue on each scan.
2. **Fix-version tracking.** Upstream records carry `affected[]` ranges and
   fix versions; consumers can auto-PR the bump. Analysis flags the pattern
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
7. **Vendor-neutral baseline.** SCA decisions reduce to *"version X has vuln
   Y"* — same answer regardless of scanner. The DB is the shared substrate
   everyone agrees on.

Plugins, MCP servers, and skills are typically *not your code*. The most
actionable signal isn't *"this third-party thing has unsafe patterns"* — it's
*"this third-party thing has a publicly disclosed vuln with a fix version."*
That is an SCA finding by definition. Agent-installation manifests
(`mcp.json`, `.claude-plugin/plugin.json`) are lockfile-equivalent: they
carry version semantics. The substrate SCA needs already exists in the
format. OpenACA is the agent-context overlay that closes the loop.

## What OpenACA adds beyond upstream OSV

OpenACA does not mint its own vulnerability IDs. Overlays are keyed by
upstream IDs (GHSA-*, CVE-*, OSV-*, PYSEC-*, MAL-*) and live at
`overlays/<upstream-id>.yaml`. See
[ADR-0009](../adrs/0009-overlay-only-v0.md).

Three things OpenACA overlays contain that a generic OSV/GHSA record does not:

1. **`taxonomies{}`** — mapping of OpenACA-owned agent-context taxonomy
   families, including `owasp_agentic_top10[]` entries referencing
   ASI01–ASI10 categories. Lets consumers triage findings by the framework
   category instead of just CVE list.
2. **`evidence_level`** — enum `confirmed | likely | research | disputed |
   withdrawn`. Lets consumers filter noise. Auto-fix on confirmed,
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

## Why this role holds up over time

Adjacent ecosystems (notably GHSA / OSV.dev) could expand to cover parts of
what OpenACA does today. The plausible expansion path is **OSV-Scanner /
Dependabot adding parsers for `mcp.json`, `.claude-plugin/plugin.json`,
etc.** That would close the *manifest-parsing* gap. OpenACA's role does
*not* depend on that gap staying open.

Two layers survive an OSV expansion:

1. **The agent-context overlay.** OSV/GHSA's data model is generic; they have
   no obvious reason to add an agent-context extension. OpenACA = the schema
   authority for reviewed agent-stack taxonomy mappings and evidence level.
   Even if OSV ingests OpenACA overlays, they do so under the
   `database_specific.openaca` namespace OpenACA defines.
2. **Non-PURL component identity.** PURL is a strong substrate for components
   distributed via npm, PyPI, GitHub releases, Docker. It does not (today)
   reach Claude Code plugins distributed via marketplace.json indirection,
   Cursor extensions distributed via Cursor's marketplace, Windsurf plugins,
   or stdio-launched MCP servers identified by command + args. OpenACA's
   manifest parsers and `ComponentRef` identity model follow install-time
   identity, not source-tree identity. Promoting these to standard PURL
   types is a future standards proposal; OpenACA's native identity moves now.

These are **operational** advantages, not structural impossibilities. The
defenses are:

- **Adoption** — first credible mover on the overlay schema becomes the
  convention.
- **Curation speed** — a focused project iterating on agent-stack overlays
  ships faster than a general-purpose security database adding bespoke
  extensions.
- **Domain specificity** — OpenACA *is* agent-infrastructure; GHSA is
  general. A consumer asking *"what's my agent-tool-hijack exposure"* gets a
  clean answer from OpenACA; from GHSA, they have to reconstruct it from
  generic CVE records.
- **Schema authority via standards proposal** — OpenACA's
  `database_specific.openaca` extension and component-identity conventions
  are candidate inputs to the OSV schema standardization process. Even if a
  successor convention is eventually upstreamed into OSV proper, OpenACA
  drove its definition.

## Why OSS

OpenACA's value depends on being the neutral, vendor-independent overlay
substrate for agent infrastructure. OSS is not a generosity choice; it is
how the substrate works.

1. **Adoption is what the role rests on.** The operational durability
   factors (adoption, curation speed, domain specificity, schema authority)
   all require ingestion to be friction-free for everyone — scanners,
   aggregators, downstream tooling, individual researchers. CC-BY-4.0
   (matching OSV.dev) is the data-license norm; anything more restrictive
   forecloses the very adoption the project depends on.
2. **Trust accrues to neutral sources.** Vuln overlays work because
   consumers trust them as neutral. A closed OpenACA forfeits the
   vendor-neutrality differentiator and looks indistinguishable from any
   single vendor's private feed.
3. **Standards leverage.** The agent-context overlay is most valuable if it
   becomes the convention everyone adopts. Standards spread from open
   canonical implementations. A closed OpenACA would prompt larger players
   to fork an open competitor and the schema-authority play disappears.
4. **Curation network effects.** Security researchers, individual
   maintainers, and vendor security teams contribute to open vuln databases
   (GHSA, OSV) but rarely to closed ones. The cost-to-value ratio of
   curation is much better with open contribution.

License decisions:

- **Source code: Apache-2.0.** Patent grant matters for a schema-and-tooling
  project that may be incorporated into larger systems. See
  [ADR-0001](../adrs/0001-licenses.md).
- **Overlay data: CC-BY-4.0.** Matches OSV.dev; avoids share-alike viral
  terms blocking mixed-license downstream consumers. See
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
consumers expect to query, not just an archive of overlays on existing
records.

## Out of scope (anywhere in this thesis)

OpenACA-the-OSS-project covers schema, overlay corpus, manifest parsers,
linter, static export, and reference scanner. It does **not** cover (now,
or in any phase of the OSS roadmap):

- Behavioral / runtime failure cataloging (different persona; covered by
  AVID and similar projects).
- Generative-AI failure evidence at large (model bias, hallucination
  taxonomies, etc. — outside scope).
- Agent-runtime monitoring or instrumented detection.
- Closed detection-rule formats specific to one scanner.
- Public benchmarking of scanners or scanner leaderboards.
- Minting vulnerability IDs in a separate `OPENACA-` namespace.

Out-of-scope items remain so even if they would clear additional value;
scope discipline is itself part of the thesis. OpenACA is *the open
agent-context overlay substrate*. Other layers in the agent-security stack
are other people's work.

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
