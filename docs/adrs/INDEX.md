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

- [ADR-0001 — Code Apache-2.0; advisory data CC-BY-4.0](0001-licenses.md): code under Apache-2.0 (patent grant matters for downstream incorporation); advisory data under CC-BY-4.0 to match OSV.dev and avoid share-alike viral terms blocking mixed-license consumers.
- [ADR-0002 — Schema extension key `database_specific.openaca`](0002-schema-extension-key.md): all OpenACA-specific fields live under this single OSV-extension key; locked from V0 because renaming the wire format breaks every cached downstream consumer.
- [ADR-0005 — V0 manifest parsers are POSIX-only](0005-manifest-parsers-posix-only.md): `_classify_command` is `Path(command).stem` (case-sensitive, no backslash/PATHEXT handling); rejected always-lowercase, Windows-shape heuristic, and known-launcher allow-list because each either introduces POSIX false positives or ships V0-out-of-scope Windows support.
- [ADR-0006 — openaca scan subcommands, claude-plugin ecosystem, attribution](0006-openaca scan-subcommands-and-attribution.md): Explicit `repo`/`endpoint` subcommand split (subcommand is **required**; no no-subcommand fallback — the back-compat shim was removed pre-launch); `endpoint` defaults to `$CLAUDE_CONFIG_DIR` else `~/.claude` and accepts optional `--project`; `claude-plugin` as a recognized `affected[*].package.ecosystem` so plugin advisories match via `_match_versioned`; `attributed_to` mirrored on ComponentRef and Finding so plans 008 and 009 can tag "via plugin X" findings.
- [ADR-0007 — Component inventory ecosystems, tiered scanning, identity scopes](0007-component-inventory-and-host-adapters.md): four new ecosystems (`claude-skill` with range matching; `claude-hook`/`claude-command`/`claude-agent` identity-only); identity-scope disambiguation (plugin-bundled / settings-scoped / repo-declared); tiered scanning model (Tier 1 ships V0; Tier 3 SDK-aware extraction deferred to V1); hooks NOT merged across scopes (blast radius differs); CLAUDE_PLUGIN_ROOT path semantics.
- [ADR-0009 — V0 ships OpenACA overlays, not OpenACA advisory IDs](0009-overlay-only-v0.md): V0 overlays use upstream IDs (`GHSA-*`, `CVE-*`) and carry only `database_specific.openaca`; scanner queries OSV.dev by PURL, dedupes by alias graph, then applies bundled overlays; no `--db` or `--overlays` CLI flag in V0.
- [ADR-0011 — Add opt-in LLM seed annotation with framework context](0011-llm-assisted-seed-annotation.md): seeders keep deterministic discovery and alias deduplication, but `openaca seed --llm-provider <openai|anthropic> --llm-model <name>` can draft OpenACA annotations from OSV records plus `docs/frameworks/*.md`; LLM output writes candidates only, never canonical overlays.
- [ADR-0012 — Keep canonical overlays minimal and standards-based](0012-minimal-overlay-schema.md): canonical overlays keep only OpenACA-reviewed taxonomy mappings, evidence level, and optional `threat_kind: malicious_package`; scanner-observed context stays in scan output, so `component_type`, `component_identity`, `surfaces`, and `agent_impact` leave the canonical schema.
- [ADR-0013 — Separate component identity from observation location](0013-non-package-component-identities.md): `ComponentRef.component_identity` is logical identity for non-package components, while `source_manifest`, `source_locator`, `attributed_to`, and `extra` carry where/how the scanner observed it; hook identities stop encoding settings scope, event, and array index as component identity.

## Superseded

- [ADR-0003 — Single namespace, type-tagged advisories](0003-single-namespace-architecture.md): superseded by ADR-0009; V0 no longer mints OpenACA advisory IDs.
- [ADR-0004 — `YYYY` in advisory IDs is assignment year, not alias year](0004-advisory-id-year.md): superseded by ADR-0009; overlay IDs are upstream IDs.
- [ADR-0008 — Lockfile dispatch, manifest fallback, OSV.dev federation](0008-lockfile-dispatch-and-osv-federation.md): superseded by ADR-0009 for federation/default DB semantics; lockfile parsing details remain historical context.
- [ADR-0010 — Use deterministic candidate seeding and nested overlay taxonomies](0010-overlay-taxonomies-and-seeding.md): superseded by ADR-0011 for seed annotation; nested taxonomy shape and candidate/promote boundary remain in force.
