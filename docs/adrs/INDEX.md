# ADR Index

Architecture Decision Records for ASVE. The index is loaded into every
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
- [ADR-0002 — Schema extension key `database_specific.asve`](0002-schema-extension-key.md): all ASVE-specific fields live under this single OSV-extension key; locked from V0 because renaming the wire format breaks every cached downstream consumer.
- [ADR-0003 — Single namespace, type-tagged advisories](0003-single-namespace-architecture.md): one `ASVE-YYYY-NNNN` ID space with a `type` discriminator (vulnerability | exposure | config); V0 ships only `type: vulnerability`, others reserved + rejected by schema until methodology lands.
- [ADR-0004 — `YYYY` in advisory IDs is assignment year, not alias year](0004-advisory-id-year.md): ASVE-2026-NNNN aliasing CVE-2025-XXXXX is correct — year tracks ASVE catalogue date, matching CVE/GHSA/RUSTSEC convention; rejected matching upstream alias year because multi-alias and ASVE-original records can't share that rule.
- [ADR-0005 — V0 manifest parsers are POSIX-only](0005-manifest-parsers-posix-only.md): `_classify_command` is `Path(command).stem` (case-sensitive, no backslash/PATHEXT handling); rejected always-lowercase, Windows-shape heuristic, and known-launcher allow-list because each either introduces POSIX false positives or ships V0-out-of-scope Windows support.
- [ADR-0006 — asve-scan subcommands, claude-plugin ecosystem, attribution](0006-asve-scan-subcommands-and-attribution.md): Trivy-style `repo`/`fs` subcommand split (no-subcommand defaults to `repo` for back-compat); `claude-plugin` as a recognized `affected[*].package.ecosystem` so plugin advisories match via `_match_versioned`; `attributed_to` mirrored on ComponentRef and Finding so plans 008 and 009 can tag "via plugin X" findings.
- [ADR-0007 — Component inventory ecosystems, tiered scanning, identity scopes](0007-component-inventory-and-host-adapters.md): four new ecosystems (`claude-skill` with range matching; `claude-hook`/`claude-command`/`claude-agent` identity-only); identity-scope disambiguation (plugin-bundled / settings-scoped / repo-declared); tiered scanning model (Tier 1 ships V0; Tier 3 SDK-aware extraction deferred to V1); hooks NOT merged across scopes (blast radius differs); CLAUDE_PLUGIN_ROOT path semantics.

## Superseded

(none yet)
