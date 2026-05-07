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

## Superseded

(none yet)
