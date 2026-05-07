# ASVE Thesis

> Companion to [`asve-v0-design.md`](asve-v0-design.md). The design doc says
> **what** V0 ships. This doc says **why** the project exists, **what gap** it
> fills, and **why the OSS authority position is durable** as adjacent
> ecosystems evolve.

## Tagline

> **Open advisories for agent stack security.**

## One-paragraph thesis

ASVE is the open, SCA-shaped advisory layer for AI agent infrastructure: it
resolves agent-installation manifests (`mcp.json`, `.claude-plugin/plugin.json`,
`.claude/settings.json`, and similar) to component identities, matches them
against OSV-compatible records, and adds agent-specific context for triage.
ASVE does for agent-stack manifests what
[OSV](https://github.com/ossf/osv-schema) /
[GHSA](https://github.com/advisories) / Dependabot do for package lockfiles,
with the extra context needed to reason about agent permissions, tools,
memory, and execution surfaces.

## The wedge

Three honest facts about what's already covered in the agent-stack security
space:

1. **Versioned-package advisories on npm/PyPI are already covered by GHSA.**
   When an MCP server is published as `@modelcontextprotocol/server-X` on npm
   or `mcp-server-Y` on PyPI, recent CVEs flow through GHSA → OSV → Dependabot.
   ASVE does not replace this pipeline. It aliases it.
2. **Agent-stack scanning is being addressed by artifact-analysis tools.**
   Several Apache-2.0 / open scanners exist that inspect MCP servers, plugin
   manifests, IDE config, and skill content for unsafe patterns. They operate
   primarily as artifact analysis tools (SAST-shaped). Their output is
   pattern-match findings, not advisories looked up against a database.
3. **OWASP Agentic Top 10 has settled the taxonomy.** Categories ASI01–ASI10
   are the standard reference. ASVE consumes this taxonomy; it does not
   redefine it.

Three honest facts about what is *not* covered:

1. **Plugin / marketplace component advisories with stable versioned
   identifiers.** Claude Code plugins (`.claude-plugin/plugin.json` carries an
   explicit semver `version`), Cursor extensions, Windsurf plugins, and
   marketplace.json registries all have versioned components, none have a
   canonical advisory database, and none are parsed by SCA tools today. This
   is the primary gap.
2. **Manifest-based component installations that bypass the lockfile.** MCP
   servers installed via `mcp.json` `command: "uvx pkg==1.4.0"` or `npx pkg`
   *can* have known CVEs against the underlying package, but standard SCA
   tooling misses them because the package never enters `package.json` /
   `requirements.txt`.
3. **A vendor-neutral, mirrorable, openly licensed corpus** that consumers
   can rely on without depending on any single scanner vendor's data
   pipeline.

ASVE's wedge sits in those three gaps: **the open, OSV-compatible advisory
database that extends SCA to plugin / MCP / marketplace manifests, with
agent-context metadata overlay and OWASP ASI category mapping.**

## SAST and SCA, layered

The agent-stack security stack has two complementary layers, just like
traditional software security.

| Layer | What it does | Knowledge source | Examples |
|---|---|---|---|
| **SAST** (artifact analysis) | Inspects an artifact for unsafe patterns | Rules + analysis logic | Pattern-checking scanners for MCP servers, plugin manifests, skill content, IDE configs |
| **SCA** (database lookup) | Identifies third-party components by version, looks them up against a vuln DB | Database + parsers | OSV-Scanner, Trivy, Dependabot for traditional packages; **ASVE for agent-stack manifests** |

These layers stack. They do not compete. Mature security tooling runs both
because they catch different signal types.

What SCA gets you that pure artifact analysis cannot:

1. **Disclosure-time alerting.** A vuln is disclosed today; tomorrow your
   manifest survey across N projects flags every install. Pure analysis would
   have to re-find the issue on each scan.
2. **Fix-version tracking.** Records carry `affected[]` ranges and fix
   versions; consumers can auto-PR the bump. Analysis flags the pattern but
   rarely tells you the path to fixed.
3. **Speed at fleet scale.** Lookup is O(N) hash-against-DB. Analysis is O(N
   × analysis-time). For an org with many agent installations, the difference
   matters.
4. **Cross-component fleet view.** *"Across our org, what is our exposure to
   ASVE-2026-NNNN?"* — answerable from a manifest survey + DB lookup.
5. **Pre-install gates.** CI workflow can ask *"is this manifest about to
   install a known-vulnerable component?"* before the install lands.
6. **Audit trail.** *"Were we exposed to ASVE-X between March and May?"* — a
   DB + lockfile history answers it.
7. **Vendor-neutral baseline.** SCA decisions reduce to *"version X has vuln
   Y"* — same answer regardless of scanner. The DB is the shared substrate
   everyone agrees on.

Plugins, MCP servers, and skills are typically *not your code*. The most
actionable signal isn't *"this third-party thing has unsafe patterns"* — it's
*"this third-party thing has a publicly disclosed vuln with a fix version."*
That is an SCA finding by definition. Agent-installation manifests
(`mcp.json`, `.claude-plugin/plugin.json`) are lockfile-equivalent: they
carry version semantics. The substrate SCA needs already exists in the
format. ASVE is the database that closes the loop.

## What ASVE adds beyond OSV

Six things ASVE's records contain that a generic OSV/GHSA record does not:

1. **`component_type`** — open-vocabulary string identifying the kind of
   agent-stack component (`mcp_server`, `claude_plugin`, `cursor_extension`,
   `agent_framework`, `model_proxy`, etc.). Different blast radii live in
   different bins.
2. **`surfaces[]`** — array enumerating attack surfaces the component
   exposes (`tool_invocation`, `stdio`, `repo_context`, `network`, `memory`,
   `filesystem`, etc.).
3. **`agent_impact{}`** — boolean table over standard agent-relevant impact
   categories (`repo_read`, `repo_write`, `credential_exfiltration`,
   `tool_hijack`, `memory_poisoning`, `pr_manipulation`, `code_execution`).
   CVSS C/I/A doesn't speak agentic.
4. **`owasp_agentic_top10[]`** — array referencing ASI01–ASI10 categories.
   Lets consumers triage findings by the framework category instead of just
   CVE list.
5. **`component_identity`** (in `database_specific.asve`) — ASVE-native
   identity for components that do not map to standard PURL ecosystems.
   Examples: `claude-plugin/<author>/<plugin>@<version>`,
   `cursor-ext/<publisher>/<ext>@<version>`,
   `mcp-stdio/<launcher>/<args-hash>`. Where standard PURLs work
   (`pkg:npm/...`, `pkg:pypi/...`, `pkg:github/...`, `pkg:docker/...`), ASVE
   uses them; the native identity covers the gap.
6. **`evidence_level`** — enum `confirmed | likely | research | disputed |
   withdrawn`. Lets consumers filter noise. Auto-fix on confirmed,
   ticket-only on research-grade.

These fields all live under `database_specific.asve` in the OSV schema. OSV
reserves that namespace exactly for per-database extensions. ASVE-aware
tooling reads the overlay; OSV-compliant generic tooling ignores it.
**Backwards-compatible by design.**

## Single namespace, type-tagged advisories

ASVE uses **one ID space** (`ASVE-YYYY-NNNN`), with each advisory carrying a
`type` field:

| `type` | Use for | V0 status |
|---|---|---|
| `vulnerability` | Versioned components with a known security flaw | ✅ V0 (only public records) |
| `exposure` | Components configured in a way that creates risk without being a strict CVE-class flaw (overpowered permission grants, default-credential templates published to marketplaces) | ⏸ V1. Schema reserves the value; rejected in V0 PRs pending methodology doc. |
| `config` | Class-level pattern advisories (e.g., *"any `mcp.json` with unversioned `uvx` invocation"*) — pattern, not specific instance | ⏸ V1 |

Why a single namespace beats the two-corpus alternative:

- One ID format to remember, one mental model.
- CVE itself doesn't split into `CVE-CFG`; just `CVE`. ASVE follows the same
  shape.
- Schema variation handled via `type` field; per-type required-field
  enforcement in the linter.
- Future-proof for new types without ID-space migration.

The "E" in ASVE (Exposures) is intentionally future-compatible, not V0
scope. See [ADR-0003](../adrs/0003-single-namespace-architecture.md).

## Why these structural moats hold

Adjacent ecosystems (notably GHSA / OSV.dev) could expand to absorb parts of
ASVE's wedge. The plausible expansion path is **OSV-Scanner / Dependabot
adding parsers for `mcp.json`, `.claude-plugin/plugin.json`, etc.** That
would close the *manifest-parsing* gap from above. ASVE's authority does
*not* depend on that gap staying open.

Three layers survive an OSV expansion:

1. **The agent-context overlay.** OSV/GHSA's data model is generic; they have
   no obvious reason to add an agent-context extension. ASVE = the schema
   authority for what makes a record *agentic* (component_type, surfaces,
   agent_impact, ASI mapping, evidence_level). Even if OSV ingests ASVE
   records, they do so under the `database_specific.asve` namespace ASVE
   defines.
2. **Class-level types.** OSV's data model is anchored on `affected[]` — a
   per-package vulnerability shape. There's no native way to represent
   *"any record matching pattern X is risky"* without a specific instance.
   `type: config` and `type: exposure` records (V1) live in a category OSV
   structurally doesn't enter. Industry parallels for class-level posture
   advisories live in vendor-specific rule sets (CIS Benchmarks, Sigma,
   Semgrep, cloud-policy engines) — none of those are GHSA. ASVE = the open
   class-level corpus for agent infrastructure.
3. **Non-PURL component identity.** PURL is a strong substrate for components
   distributed via npm, PyPI, GitHub releases, Docker. It does not (today)
   reach Claude Code plugins distributed via marketplace.json indirection,
   Cursor extensions distributed via Cursor's marketplace, Windsurf plugins,
   or stdio-launched MCP servers identified by command + args. ASVE's
   `component_identity` field follows install-time identity, not source-tree
   identity. Promoting these to standard PURL types is a future standards
   proposal; ASVE's native identity moves now.

These are **operational** advantages, not structural impossibilities. The
defenses are:

- **Adoption** — first credible mover on the schema becomes the convention.
- **Curation speed** — a focused project iterating on agent-stack records
  ships faster than a general-purpose security database.
- **Domain specificity** — ASVE *is* agent-infrastructure; GHSA is general.
  A consumer asking *"what's my agent-tool-hijack exposure"* gets a clean
  answer from ASVE; from GHSA, they have to reconstruct it from generic CVE
  records.
- **Schema authority via standards proposal** — ASVE's `database_specific.asve`
  fields and component-identity conventions are candidate inputs to the
  OSV schema standardization process. Even if a successor convention is
  eventually upstreamed into OSV proper, ASVE drove its definition.

## Why OSS

ASVE's value depends on being the neutral, vendor-independent advisory
substrate for agent infrastructure. OSS is not a generosity choice; it is
how the substrate works.

1. **Adoption is the moat we just identified.** Operational defenses
   (adoption, curation speed, domain specificity, schema authority) require
   ingestion to be friction-free for everyone — scanners, aggregators,
   downstream tooling, individual researchers. CC-BY-4.0 (matching OSV.dev) is
   the data-license norm; anything more restrictive forecloses the very moat
   the thesis depends on.
2. **Trust accrues to neutral sources.** Vuln databases work because
   consumers trust them as neutral. A closed ASVE forfeits the
   vendor-neutrality differentiator and looks indistinguishable from any
   single vendor's private feed.
3. **Standards leverage.** The agent-context overlay is most valuable if it
   becomes the convention everyone adopts. Standards spread from open
   canonical implementations. A closed ASVE would prompt larger players to
   fork an open competitor and the schema-authority play disappears.
4. **Curation network effects.** Security researchers, individual
   maintainers, and vendor security teams contribute to open vuln databases
   (GHSA, OSV) but rarely to closed ones. The cost-to-value ratio of
   curation is much better with open contribution.

License decisions:

- **Source code: Apache-2.0.** Patent grant matters for a schema-and-tooling
  project that may be incorporated into larger systems. See
  [ADR-0001](../adrs/0001-licenses.md).
- **Advisory data: CC-BY-4.0.** Matches OSV.dev; avoids share-alike viral
  terms blocking mixed-license downstream consumers. See
  [ADR-0001](../adrs/0001-licenses.md).

## V0 → V1 expansion path

| Phase | Scope |
|---|---|
| **V0** | `type: vulnerability` only. Mostly aliases to existing CVE/GHSA records, with at least one *enriched* record demonstrating manifest-detection beyond lockfile. Schema, linter, ID reservation, static export, reference scanner (CLI + Action). Disclosure policy doc; private-pilot operation only. See [`asve-v0-design.md`](asve-v0-design.md) for the canonical V0 deliverable list. |
| **V1 entry** | Active disclosure pipeline. Triggered when V0 ships ≥ 25 advisories across 3+ component types, the disclosure framework is documented + tabletop-rehearsed, and ≥ 5 external ecosystem signals materialize. |
| **V1 scaling** | Programmatic disclosure sweeps; first real coordinated disclosure completed end-to-end. |
| **V1 record types** | `type: exposure` (overpowered configurations, default-credential templates) and `type: config` (class-level patterns) become published-record categories with documented methodology. |
| **V1 manifest coverage** | Cursor + Windsurf + ChatGPT-style plugin manifests join the V0 set (`package.json`, `mcp.json`, `.claude-plugin/plugin.json`, `.claude/settings.json`). |
| **V1 hash-based identity** | `T3` advisories — content-hash-keyed records for skill manifests, exported flows, and copy-pasted IDE rule templates. |

V0 is the credibility floor. V1 is when ASVE becomes the corpus consumers
expect to query, not just an archive of aliases.

## Out of scope (anywhere in this thesis)

ASVE-the-OSS-project covers schema, advisory corpus, manifest parsers, ID
reservation, linter, static export, and reference scanner. It does **not**
cover (now, or in any phase of the OSS roadmap):

- Behavioral / runtime failure cataloging (different persona; covered by
  AVID and similar projects).
- Generative-AI failure evidence at large (model bias, hallucination
  taxonomies, etc. — outside scope).
- Agent-runtime monitoring or instrumented detection.
- Closed detection-rule formats specific to one scanner.
- Public benchmarking of scanners or scanner leaderboards.

Out-of-scope items remain so even if they would clear additional value;
scope discipline is itself part of the thesis. ASVE is *the open advisory
substrate*. Other layers in the agent-security stack are other people's
work.

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
- ASVE V0 design: [`asve-v0-design.md`](asve-v0-design.md)
- ADR-0001 — Licenses: [`../adrs/0001-licenses.md`](../adrs/0001-licenses.md)
- ADR-0002 — Schema extension key:
  [`../adrs/0002-schema-extension-key.md`](../adrs/0002-schema-extension-key.md)
- ADR-0003 — Single namespace architecture:
  [`../adrs/0003-single-namespace-architecture.md`](../adrs/0003-single-namespace-architecture.md)
