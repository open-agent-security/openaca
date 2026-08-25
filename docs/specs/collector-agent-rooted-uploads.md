# Collector Agent-Rooted Uploads — Design

Companion ADRs: [0050](../adrs/0050-collector-upload-cardinality.md) (upload
cardinality), [0051](../adrs/0051-redaction-covers-bom-metadata.md) (redaction scope).

The agent model — what a kind is, what the four `metadata.component` properties mean,
how coverage resolves, what a `bom-ref` looks like — is specified in
[Multi-Agent Support](multi-agent-support.md) and enforced by
`schema/openaca-bom.schema.json`. **This document adds only what is specific to the
upload boundary.** Read that one for the wire format.

## The change

The local scan path emits agent-rooted BOMs. The collector does not. This change makes
an upload the same document a local scan produces.

| | `scan endpoint` (already migrated) | `remote sync endpoint` — today | `remote sync endpoint` — after |
|---|---|---|---|
| Discovery | `discover_agents` + `build_agent_graph` per agent | `build_graph(config_dir, mode="endpoint")` | `discover_agents` + `build_agent_graph` per agent |
| Root `bom-ref` | `root/claude-code` | `openaca:target` | `root/claude-code` |
| `openaca:schema_version` | `0.5` | `0.4` | `0.5` |
| `metadata.component` | the agent, with four properties | a synthetic target, `openaca:component_type: target` | the agent, with four properties |
| `openaca:target_type` | not written | `endpoint` — the last emitter in the codebase | not written |
| `openaca:target` | `str(agent.config_root)` — an absolute path | `endpoint:user-scope` | **not written** |
| Posture / observations | resolved per agent | one flat set for the endpoint | resolved per agent |
| Documents per invocation | one BOM per agent | one upload, always | one upload per agent |

Read the last column against the first: they agree everywhere except `openaca:target`,
and that one row is the whole reason this document exists rather than a one-line
instruction to reuse the scan path. Everything else is *make the upload match the scan*;
`openaca:target` is *do not* — see [The upload writes no
target](#the-upload-writes-no-target).

**One release, no compatibility layer.** No dual emission, no `--legacy` flag, no
version negotiation, no diff shim. How the hosted side treats documents stored before
this release is the hosted side's own concern and is not coordinated here.

## What changes in the collector

### Discovery replaces the mode string

`build_graph(config_dir, mode="endpoint")` becomes
`discover_agents(DiscoveryContext(source="installed", config_dir=..., project_root=...))`
followed by `build_agent_graph(agent)` per agent — the same pair `tools/scan.py` uses
for `scan endpoint`. This removes the last live caller of `build_graph`'s `mode`
parameter, which [Multi-Agent Support](multi-agent-support.md) named as the reason it
could not be deleted outright.

The BOM call gains `agent_kind`, `agent_id`, `agent_name`,
`composition_source: installed`, and `composition_coverage`, and drops `target_type`.

### Posture and observations resolve per agent

Each agent's findings come from its own graph, so a component loaded by two agents
produces two findings — one per agent, each carrying the agent it belongs to, and each
with a `component_bom_ref` that resolves inside that agent's document.

Posture prep reads the kind's `installed_posture_collectors` rather than calling
`collect_endpoint_mcp_manifests` / `collect_endpoint_settings_manifests` directly, so a
second installed kind is not scanned with Claude Code's semantics. `_agent_scan_prep`
in `tools/scan.py` shows the shape but should **not** be reused: it also builds
render-only fields (`target_rows`, `next_actions`) the collector has no use for.

### One asset, N uploads

A sync registers one asset — the machine, keyed on hostname — and sends one upload per
agent. See [ADR-0050](../adrs/0050-collector-upload-cardinality.md).

No new envelope field is needed: the hosted side resolves the agent from
`metadata.component` in the document, never from registration. Partial failure needs no
special handling either — an agent whose upload fails on the network lands in the
pending cache and retries on the next sync, while the other agents' state stays current,
because each agent is tracked separately.

### The privacy contract extends to BOM metadata

The four agent properties land in `metadata.component.properties`, and `openaca:target`
already sits in `metadata.properties`. **No privacy layer scans `metadata`** — not the
collector's `_redact_payload_for_remote`, not `enforce_remote_upload_contract`, and not
the hosted validator. All three walk `bom.components[*].properties` and stop.

That gap predates this change and is harmless today only because the one metadata value
the collector writes is a literal it constructs itself. It stops being harmless the
moment the collector writes metadata derived from the machine. Closing it is therefore
part of this change and must land **before** anything writes there. See
[ADR-0051](../adrs/0051-redaction-covers-bom-metadata.md).

The boundary is **every string in `bom.metadata` that the collector synthesizes** —
three locations: `metadata.properties`, `metadata.component.properties`, and
`metadata.component.name`. The last one carries no `openaca:` prefix and so is covered
by name rather than by filter. It is emitted from `AgentInstance.display_name`, typed as
an unconstrained `str`; a future kind can put anything there, and it is the literal
`Claude Code` today only because the one shipped kind hardcodes it. Selecting the
property lists by `openaca:` prefix is a mechanism, not the definition of the boundary.

### The upload writes no target

`scan endpoint` passes `target=str(agent.config_root)` — an absolute path, correct
locally under ADR-0003 because the OSS CLI runs on the user's own machine. The upload
path passes `target=None`, so an uploaded BOM carries no `openaca:target` property at
all. The upload envelope's `target_locator` is unchanged.

Uploads substitute a neutral literal today, and carrying that forward was the obvious
move. It is the wrong one. `endpoint:user-scope` is a **constant** — the same value from
every machine on every sync, so it distinguishes nothing — it duplicates the envelope's
`target_locator`, which the hosted side already stores, validates, and surfaces, and it
keeps the word `endpoint` alive in the same metadata block this change removes
`openaca:target_type` from.

Nothing reads it, now or by design. The schema requires only `openaca:target_type`. The
hosted backend touches `bom.metadata` in exactly one place — `metadata.component`'s
`bom-ref`, to record the BOM's root — and never reads `metadata.properties` at all. And
the hosted side's own re-rooting work resolves an agent from `metadata.component` alone,
naming `openaca:target` among the things it explicitly declines to infer from. Dropping
the property removes a temptation that had to be guarded against in writing.

**Not to be confused with the root `bom-ref`.** The literal string `openaca:target` is
also the root node's key in a pre-agent document, and *that* use is consumed — it anchors
dependency edges and attribution walks, and stored `0.4` documents keep resolving through
it. Only the `metadata.properties` entry of the same name goes away.

The mistake to avoid is still porting `target=str(agent.config_root)` from the scan call
site, where it is correct and where a reviewer sees no reason for the divergence. It now
produces a property that should not exist rather than a leaked home path.

One visible cost: `scan bom` on a downloaded upload renders an empty target in its
inventory-tree header, because that value is read straight from this property
(`tools/scan.py:1696`). Stored `0.4` documents are unaffected — they keep the property
and the reader that resolves it.

## Invariants to protect

- An uploaded BOM writes no `openaca:target`, and the envelope's `target_locator` is
  unchanged. This needs a test that fails against a verbatim port of the scan call site.
- No absolute path reaches the wire from any string the collector synthesizes into
  `bom.metadata` — `metadata.properties`, `metadata.component.properties`, and
  `metadata.component.name`. The last is not `openaca:`-prefixed and needs its own
  adversarial test; asserting only via the contract enforcer is self-referential,
  because the enforcer's scope is the thing under test.
- `openaca:composition_source` is present and explicit on every uploaded BOM; a
  singleton kind omits `openaca:agent_id` rather than emitting it empty.
- `content_hash` is computed after redaction, per payload.
- A sync uploads every agent it discovered, or reports which ones it could not.

## Verification

`openaca remote sync endpoint --dry-run` prints the payloads a sync would send, without
network I/O, after redaction and contract enforcement. It is the before/after check for
this change: today it prints `0.4` with root `openaca:target` and
`openaca:target_type: endpoint`; afterwards it must print `0.5` with root
`root/claude-code`, the four agent properties, and neither `openaca:target_type` nor
`openaca:target` in `bom.metadata.properties`.

## Out of scope

- **Hosted-side re-rooting.** Built in parallel, not coordinated through this document.
- **Any second agent kind.** This migrates the upload path for the one kind that exists.
- **Retiring `build_graph`'s `mode` parameter.** This removes its last caller, which
  makes the removal possible; doing it is a separate cleanup.
- **Hostname or place identity in the document.** Unchanged — it stays in the
  registration envelope (ADR-0003, ADR-0045).
