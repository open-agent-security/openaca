# Agent Component Capability Extraction

*Design spec. Status: proposed (2026-07-03). Companion ADR amends ADR-0022
(Agent BOM boundary); implementation plan follows.*

## Goal

Give each agent component a structured, evidence-backed description of **what it
can do** — read files, run shells, egress to the network, touch credentials,
access sensitive data. Today OpenACA computes a few capability-adjacent signals
as posture findings (skill `allowed-tools`, insecure transport, mutable install)
but has no first-class capability model. Capability facts are the missing input
that lets the composition graph answer *"which component is risky, and why"* —
e.g. "the vulnerable package sits inside a plugin whose MCP server can run shells
and read credentials" — rather than only "a vulnerable package exists."

Capabilities are **descriptors** (what a component is / can do). They are consumed
by an exposure layer that ranks and explains risk. This spec defines the
descriptors and how they are extracted; the exposure ranking/report that consumes
them is a separate concern (see Non-goals).

## Principles

1. **Absence is not falsehood.** A missing capability is not proof the component
   lacks it, and a non-empty capability list is rarely exhaustive. Every
   component carries a `capability_coverage` marker; most v1 extraction is
   `partial`. OpenACA never implies "these are all the capabilities" when it only
   established "these are the capabilities we could defend."
2. **Declining beats guessing.** Assert a capability only with citable evidence.
   Uncovered components are `unknown`, not silently empty. (This is the ADR-0039
   lesson applied to capabilities: a heuristic over open-ended source that guesses
   produces false descriptors, which are worse than gaps.)
3. **Capabilities are descriptors, not decisions.** Capability facts describe a
   component; exposure scores, rankings, and cards are derived decisions and live
   outside the BOM (ADR-0022 boundary; see BOM emission).
4. **Claim type is orthogonal to source** (ADR-0035). Each capability records
   *how* it was established (`method`) separately from *who* asserted it
   (`source`).

## Capability model

### Capabilities vs. risk modifiers

Two distinct claim families — kept separate so different claims don't blur:

- **Capabilities** — what the component can do. Closed taxonomy (extended only by
  ADR): `file_read`, `file_write`, `shell_exec`, `network_egress`,
  `credential_access`, `sensitive_data_access`.
- **Risk modifiers / context** — attributes that change how much a capability
  matters, but are *not* capabilities. `execution_locus` (local vs remote) is
  recorded **per capability**, as a qualifier for where that capability executes;
  the rest — transport (http/https), auth posture, install mutability,
  active/enabled, operator/domain — are **component-level** context. All feed
  exposure ranking; several already exist as posture signals.

`execution_locus` is the pivotal modifier: a `file_read` on a **local** MCP reads
the developer's files (local blast radius); the same on a **remote** MCP reads
the operator's files (the risk is the data egressed *to* the operator, not local
access). Remote components therefore carry capabilities `{network_egress,
sensitive_data_access}` with `execution_locus: remote` plus modifiers
`{operator, transport, auth}` — a data-sharing relationship, not local access.

### Data shape

Each graph node / `ComponentRef` gains an optional capability block:

```yaml
capability_coverage: partial        # unknown | partial | complete
capabilities:
  - name: shell_exec                 # closed taxonomy
    execution_locus: local           # local | remote
    method: declared                 # declared | curated | inferred  (claim type)
    source: openaca                  # who asserted it (ADR-0035); may be external later
    source_version: "0.4.0"
    confidence: high                 # high | medium | low
    evidence:
      - kind: manifest_field
        path: SKILL.md
        field: allowed-tools
        value: "Bash(*)"
```

- `confidence` is per-capability ("how sure this capability is real").
- `capability_coverage` is per-component ("how complete our picture is") — a
  distinct axis; a `high`-confidence `shell_exec` can coexist with `partial`
  coverage. It records **whether a reading mechanism applied**, never whether
  that mechanism found anything: a component whose declaration was read and
  names none of the taxonomy is `partial` with an empty list. Only a component
  no mechanism could read is `unknown`. Deriving coverage from an empty result
  collapses the two and makes every component's silence uninformative, which is
  what a divergence rule (observed and not declared) has to subtract from.
- `method` maps to the extraction tier; `source`/`source_version` are the
  orthogonal attribution.
- `evidence` is a structured list of citable observations.

## Extraction tiers

Populated in priority order; each capability keeps its own `method`/`source`.

1. **Declared (v1).** Read capabilities that a manifest states directly:
   - skills: `allowed-tools` (already parsed by `posture/rules/skill_capability.py`)
     → `shell_exec` / `file_write` / `file_read` etc.
   - hooks: the shell command in `ref.extra["command"]` → `shell_exec`
     (+ `network_egress` only when the command *invokes* a network client, not on
     a bare URL substring). Evidence cites the manifest locator (`path`/`field`)
     and, for egress, the matched client token — never the raw command body,
     which is user/attacker-influenced and can carry secrets that would leak into
     a shared BOM. Slash commands and
     subagents are **not** mapped here: `tools/parsers/claude_command_agent.py`
     emits those refs with only `scope_owner` + `component_type` in `extra` —
     there is no shell command string to cite as evidence, and the markdown
     prompt body is attacker-influenced content, not a declared signal.
   - MCP servers: transport, auth, mutability as modifiers; for **remote** MCPs,
     `network_egress` + `sensitive_data_access` are true by construction, with
     `operator` from the URL.
   `method: declared`, `confidence: high`, evidence = the manifest field.
2. **Curated capability overlay (v1 scaffold + seeds).** A reviewed corpus keyed
   by component identity (see Corpus). Primary source for local-package MCP
   capabilities and for named remote services. `method: curated`.
3. **Bounded deterministic source analysis (deferred).** When a package's source
   is locally available (name-matched manifest, or the Phase-2 on-disk cache from
   ADR-0039), detect capabilities from specific, citable signals (imports/uses of
   `child_process`/`fs`/`net`/`subprocess`/`requests`, reads of credential paths,
   …) → capability + evidence line. Conservative: assert only what a pattern
   supports; everything else stays `unknown`. Also verifies/augments the
   identity-level overlay against the installed version (drift-catch).
   `method: inferred`, `confidence: medium`.
4. **Assisted drafting (deferred).** A model may *draft* a capability read from
   source / public docs / declared tool descriptions for **manual review**; the
   reviewed result becomes a curated overlay entry. Model output is never emitted
   directly as a capability claim.

**MCP tool enumeration is explicitly out of scope.** Reading a server's
`tools/list` requires starting the server — a runtime action, and executing the
untrusted component under assessment. Declared tool *descriptions*, where
statically available, are attacker-controllable and are treated as evidence, not
ground truth.

## Curated capability corpus

- Location: the `capabilities/` tree, a corpus **distinct from** the advisory
  `overlays/` — capabilities are not advisories (different version semantics,
  review cadence, and evidence model). Discovery is **recursive** (`rglob("*.yaml")`,
  matching how `overlays/` loads), and the lookup key is read from each record's
  `identity` field, never from the file path — so a
  file may be named for a sanitized identity (`mcp-server-filesystem.yaml`) or
  nested (`npm/@scope/name.yaml`) without the loader silently skipping it. The
  corpus ships in the wheel (`force-include`) so installed/Action runs resolve it.
- **Keyed by `openaca:identity`** (ADR-0042) — e.g.
  `mcp-server/npm/@modelcontextprotocol/server-filesystem` or
  `package/npm/@scope/name`. Package-backed MCP identities are source-derived,
  so local aliases and version pins resolve to the same corpus key without a
  parallel match-coordinate index.
- **Version-independent by default.** Capabilities are a coarse, slowly-changing
  behavioral property; they are near-monotonic (added far more often than
  removed), so a version-independent record's error is asymmetric (it may
  understate a newer release, rarely overstate). One record per identity — not a
  `(package, version)` matrix.
- **Version ranges are deferred, not a v1 field.** Enforcing "this capability
  only applies from version X on" needs ecosystem-aware version comparison, and
  identities here are not all PURL-shaped (ADR-0042 keys them version-stripped
  by design). Real drift-catch — checking a curated record against the
  installed version — is tier-3 (source analysis) territory; see Extraction
  tiers.
- **`last_reviewed` + `reviewed_version` stamp** on every record, so a report can
  say honestly: "capabilities per review of v1.2; installed is v2.0 — may differ."
  These describe the *review*, distinct from the emitted capability's
  `source_version` (the OpenACA release that asserts it, same convention as the
  declared tier) — the loader carries `reviewed_version`/`last_reviewed` into
  each capability's evidence rather than overloading `source_version` with them,
  so the drift signal above survives into the descriptor.
- A JSON schema + linter mirror the overlay discipline (schema validation,
  identity format, evidence presence).

## BOM emission (amends ADR-0022)

ADR-0022 defines the Agent BOM as composition-only (components, source
provenance, identities, edges; findings/posture live outside). Capabilities fit
that boundary **as component descriptors** — the same category as identity and
provenance — and are emitted as `openaca:capabilities` metadata on the
component, carrying `method`/`source`/`confidence`/`evidence` and the
component's `capability_coverage` (a separate `openaca:capability_coverage`
property).

What does **not** enter the BOM: exposure scores, rankings, or "why it matters"
cards. Those are derived decisions (scan-report / exposure-layer data that
*references* BOM components), consistent with how findings are handled today. The
companion ADR records this amendment explicitly.

Adding `openaca:capabilities` changed the emitted BOM shape in schema `0.3`.
ADR-0042 subsequently advances the schema to `0.4` for optional source-stable
identity. The release skill's schema-drift check gates either change, and the
lint schema accepts prior versions per the established backward-read policy.

## Consumer (context, not part of this spec)

Capabilities are an input to an exposure layer that ranks components
(severity × lineage × capability × modifiers) and explains "why it matters."
That layer is specified separately; this spec only guarantees the descriptors it
consumes. The scan/BOM remains the evidence layer; exposure is the decision layer.

## Testing

- Unit per extractor: declared (skill/hook/command/MCP fixtures → expected
  capabilities + evidence); curated-overlay lookup by identity; (deferred)
  source-analysis pattern tests.
- Corpus linter tests: schema validity, identity format, evidence required,
  duplicate identity handling.
- `tests/test_e2e.py`: a fixture repo where a skill declaring `Bash` and an MCP
  with a curated capability entry produce capability-annotated components in the
  Agent BOM, with correct `method`/`source`/`coverage`.

## v1 scope

**Ships:** the capability model (data shape, taxonomy, coverage,
capability/modifier split); tier-1 declared extraction; the `capabilities/`
corpus scaffold (schema + linter + a small seed set for common MCP servers);
`openaca:capabilities` BOM emission with the schema-version bump.

**Deferred (own specs/plans):** tier-3 source analysis (depends on ADR-0039
Phase-2 on-disk cache / local source), tier-4 assisted drafting, broad corpus
coverage, and the exposure ranking/report consumer.

## Non-goals

- Running or connecting to any component to enumerate behavior (static-first).
- Exposure scoring, ranking, or report rendering (separate decision layer).
- Model-generated capability claims emitted without manual review.
- A `(package, version)` capability matrix.
