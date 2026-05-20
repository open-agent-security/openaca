# ADR Index

Architecture Decision Records for OpenACA. The index is loaded into every
session by a hook; full ADRs are read on-demand when an entry looks
related to the work at hand.

**When to read full ADRs:** before changing logic in the area an ADR
covers. Each entry's one-liner is a hook — if it sounds at all
adjacent to your task, click through.

**When to write a new ADR:** when you make a decision where the
rejected alternative is *plausible* and *likely to be re-suggested*,
and the reason isn't obvious from the code. See `TEMPLATE.md`.

**Supersession discipline:** ADRs are immutable once accepted. If a
later decision contradicts an existing ADR, write a NEW ADR with
`supersedes: NNNN` in its frontmatter, and update the old ADR's
frontmatter to `status: superseded` + `superseded-by: NNNN`. Never
edit an accepted ADR's body in place — old PRs need to be readable
against the rules in effect at the time.

## Active

- [ADR-0005 — V0 manifest parsers are POSIX-only](0005-manifest-parsers-posix-only.md): `_classify_command` is `Path(command).stem` (case-sensitive, no backslash/PATHEXT handling); rejected always-lowercase, Windows-shape heuristic, and known-launcher allow-list because each either introduces POSIX false positives or ships V0-out-of-scope Windows support.
- [ADR-0006 — openaca scan subcommands, claude-plugin ecosystem, attribution](0006-openaca-scan-subcommands-and-attribution.md): Explicit `repo`/`endpoint` subcommand split (subcommand is **required**; no no-subcommand fallback — the back-compat shim was removed pre-launch); `endpoint` defaults to `$CLAUDE_CONFIG_DIR` else `~/.claude` and accepts optional `--project`; `claude-plugin` as a recognized `affected[*].package.ecosystem` so plugin advisories match via `_match_versioned`; `attributed_to` mirrored on ComponentRef and Finding so plans 008 and 009 can tag "via plugin X" findings.
- [ADR-0009 — V0 ships OpenACA overlays, not OpenACA advisory IDs](0009-overlay-only-v0.md): V0 overlays use upstream IDs (`GHSA-*`, `CVE-*`) and carry only `database_specific.openaca`; scanner queries OSV.dev by PURL, dedupes by alias graph, then applies bundled overlays; no `--db` or `--overlays` CLI flag in V0.
- [ADR-0011 — Add opt-in LLM seed annotation with framework context](0011-llm-assisted-seed-annotation.md): seeders keep deterministic discovery and alias deduplication, but `openaca seed --llm-provider <openai|anthropic> --llm-model <name>` can draft OpenACA annotations from OSV records plus `docs/frameworks/*.md`; LLM output writes candidates only, never canonical overlays.
- [ADR-0012 — Keep canonical overlays minimal and standards-based](0012-minimal-overlay-schema.md): canonical overlays keep only OpenACA-reviewed taxonomy mappings, evidence level, and optional `threat_kind: malicious_package`; scanner-observed context stays in scan output, so `component_type`, `component_identity`, `surfaces`, and `agent_impact` leave the canonical schema.
- [ADR-0013 — Separate component identity from observation location](0013-non-package-component-identities.md): `ComponentRef.component_identity` is logical identity for non-package components, while `source_manifest`, `source_locator`, `attributed_to`, and `extra` carry where/how the scanner observed it; hook identities stop encoding settings scope, event, and array index as component identity.
- [ADR-0014 — Schema extension key `database_specific.openaca`](0014-rename-extension-key-to-openaca.md): supersedes ADR-0002; the wire-format namespace renames from `asve` to `openaca` to match the project rename. Pre-V0, no published consumers; the lock-from-V0 rule rolls forward to the new key.
- [ADR-0015 — Overlay-data CC-BY-4.0 declared inline in README](0015-overlay-data-cc-by-inlined-in-readme.md): supersedes ADR-0001's repo-root `LICENSE-DATA` requirement; license choices (Apache-2.0 code / CC-BY-4.0 overlay data) unchanged, but the data-license declaration moves to README to match OSV.dev / GHSA / osv-scanner conventions and to avoid GitHub's "unknown license" sidebar for `LICENSE-DATA`.
- [ADR-0016 — Separate agent component source identity from scan context in output](0016-agent-component-identity-and-scan-output.md): scan output has three layers — component identity, source identity, and scan context; matching uses source identity, while `declared_by`, `component_path`, and `active_in` explain direct vs plugin-bundled installation.
- [ADR-0017 — Endpoint scan keeps project context opt-in, with an unconditional reminder note](0017-endpoint-scan-cwd-project-default.md): amends ADR-0006 by adding an always-shown note when `--project` is omitted from endpoint scans (educational mechanism to help testers discover the flag without surprise). Rejects cwd-as-default (recursive home-tree walk + double-counting), overlap-guard heuristics, and cwd-marker hint detection in favor of a single uniform rule: opt-in via `--project`, note when omitted. The `detected config_dir=..., project=...` line is also now emitted unconditionally for scan-scope transparency.
- [ADR-0019 — Separate source ecosystems from agent component types](0019-source-ecosystems-and-agent-component-types.md): supersedes ADR-0018; `ecosystem` is reserved for source naming/versioning spaces such as npm, PyPI, github, and docker, while skill/plugin/hook/command/agent/MCP server live in `component_type`; source-less agent components match only by explicit OpenACA component identity, with pre-release compatibility for legacy `skill`, `claude-skill`, and `claude-plugin` affected ecosystems.
- [ADR-0020 — Remote MCP server inventory via `mcp-remote` identity namespace](0020-remote-mcp-server-inventory.md): extends the MCP parser to emit refs for URL-bearing entries (HTTP/SSE/streamableHttp) under `mcp-remote/<normalized-host-path>` identity, source-less per ADR-0019; closes the round-1 "0 MCP servers" interpretability gap, parallels the existing `mcp-stdio/` namespace, records transport in `extra.transport` and original URL in `extra.url`. No OSV federation for remote MCPs.
- [ADR-0021 — Use skills CLI lockfiles as source provenance for direct skills](0021-skills-lock-source-provenance.md): endpoint scans keep direct skills direct, preserve activation paths, and use global `.skill-lock.json` / project `skills-lock.json` only as scanner-observed source provenance under `extra.source_provenance`; `attributed_to` remains plugin-only and recovered skill source does not affect advisory matching in V0.

## Superseded

- [ADR-0018 — Generic skill ecosystem and Agent BOM-compatible metadata](0018-generic-skill-ecosystem-and-agent-bom-fields.md): superseded by ADR-0019; the generic-skill conclusion remains, but `skill` is a component type, not a source ecosystem.
- [ADR-0007 — Component inventory ecosystems, tiered scanning, identity scopes](0007-component-inventory-and-host-adapters.md): superseded by ADR-0018 for skill ecosystem naming. The tiered scanning model, endpoint/application framing, identity-scope disambiguation, and host-adapter deferrals remain historical context and roll forward unless contradicted by ADR-0018 or ADR-0019.
- [ADR-0001 — Code Apache-2.0; advisory data CC-BY-4.0](0001-licenses.md): superseded by ADR-0015 for file-structure (license declarations move from `LICENSE-DATA` to inline in README). The Apache-2.0 / CC-BY-4.0 license choices themselves are unchanged.
- [ADR-0002 — Schema extension key `database_specific.asve`](0002-schema-extension-key.md): superseded by ADR-0014; namespace renamed to `database_specific.openaca` pre-V0.
- [ADR-0003 — Single namespace, type-tagged advisories](0003-single-namespace-architecture.md): superseded by ADR-0009; V0 no longer mints OpenACA advisory IDs.
- [ADR-0004 — `YYYY` in advisory IDs is assignment year, not alias year](0004-advisory-id-year.md): superseded by ADR-0009; overlay IDs are upstream IDs.
- [ADR-0008 — Lockfile dispatch, manifest fallback, OSV.dev federation](0008-lockfile-dispatch-and-osv-federation.md): superseded by ADR-0009 for federation/default DB semantics; lockfile parsing details remain historical context.
- [ADR-0010 — Use deterministic candidate seeding and nested overlay taxonomies](0010-overlay-taxonomies-and-seeding.md): superseded by ADR-0011 for seed annotation; nested taxonomy shape and candidate/promote boundary remain in force.
