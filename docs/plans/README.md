# OpenACA V0 — Implementation Plan Index

V0 implementation: plans 001-006 are the original V0 deliverables. Plans
007+ are pre-V0-launch refinements that fill in gaps surfaced from
dogfooding. Each plan produces working, testable software on its own.
Run them in order; later plans depend on earlier ones unless noted.

| # | Plan | Status | Depends on |
|---|---|---|---|
| 001 | [Schema + advisory-authoring tooling](001-schema-and-tooling.md) | ✅ Done | — |
| 002 | [First V0 advisories + OSV importer](002-first-advisories.md) | ✅ Done | 001 |
| 003 | [Manifest parsers](003-manifest-parsers.md) | ✅ Done | 001 (project setup) |
| 004 | [Static export pipeline](004-static-export.md) | ✅ Done | 001, 002 |
| 005 | [Reference GitHub Action](005-reference-action.md) | ✅ Done | 003, 004 |
| 006 | [Disclosure policy doc](006-disclosure-policy.md) | ✅ Done | — |
| 007 | [fs-mode foundation: CLI split, attribution, claude-plugin matcher](007-fs-mode-cli-and-attribution.md) | ✅ Done | 001, 003, 005 |
| 008 | [Component inventory: declared (repo) and active (fs) agent stack](008-component-inventory.md) | ✅ Done | 007 |
| 009 | [Plugin-internal implementation deps (lockfile transitive scanning) + OSV.dev federation](009-plugin-internal-deps.md) | ✅ Done | 008 |
| 010 | [Seed overlay pipeline](010-seed-overlay-pipeline.md) | ✅ Done | 009 |
| 011 | [Minimal overlay schema](011-minimal-overlay-schema.md) | ✅ Done | 010 |
| 012 | [Candidate annotation surface lock](012-candidate-annotation-surface-lock.md) | ✅ Done | 011 |
| 013 | [Rename ASVE → OpenACA](013-rename-asve-to-openaca.md) | ✅ Done | — |
| 014 | [Posture findings (scanner-side hygiene rules)](014-posture-findings.md) | ✅ Done | 007, 008, 009 |
| 015 | [Agent component identity + scan output](015-agent-component-identity-and-scan-output.md) | ✅ Done | 013, 014 |
| 016 | [Claude Code parser coverage](016-claude-code-parser-coverage.md) | ✅ Done | 008, 009 |
| 017 | [Generic skill ecosystem](017-generic-skill-ecosystem.md) | ✅ Done | 008 |
| 018 | [Source ecosystem and component type cleanup](018-source-ecosystem-component-type.md) | ✅ Done | 017 |
| 019 | [Remote MCP server inventory](019-remote-mcp-inventory.md) | ✅ Done | 018 |
| 020 | [Skill lock source provenance](020-skill-lock-source-provenance.md) | ✅ Done | 018 |
| 021 | [Agent BOM](021-agent-bom.md) | ✅ Done | 018, 020 |
| 022 | [First-run inventory output (product-shaped default text)](022-first-run-inventory-output.md) | ✅ Done | 015, 021 |
| 023 | [Risk Attribution (containment-aware findings)](023-risk-attribution.md) | ✅ Done | 022 |
| 024 | [bun.lock parsing (npm transitive deps for bun MCP plugins)](024-bun-lock-parsing.md) | ✅ Done | 023 |
| 025 | [OSV-native query semantics (GitHub commit / Git tag / Docker skip)](025-osv-query-semantics.md) | ✅ Done | 021 |
| 026 | [openaca.core consumption facade](026-openaca-core-facade.md) | ✅ Done | 025 |

Status legend: 🟡 active · ✅ done · ⏸ pending · 🔴 blocked.

Keep at most **one** plan in 🟡 Active state at a time. Updating a plan's
status here is the source of truth for project progress alongside checkbox
state inside individual plans.

## Identity lifecycle checklist

Any plan that changes `ComponentRef.ecosystem`, `name`, `version`, `purl`,
`component_identity`, or `extra.install_source` must include an explicit sink
audit before implementation is marked complete:

| Surface | Required check |
|---|---|
| Parser | Emits the expected `ComponentRef` fields. |
| PURL | `ComponentRef.purl` is correct, or intentionally absent. |
| Agent BOM | CycloneDX serialization and `scan bom` round-trip preserve identity. |
| Renderer | `scan repo`, `scan endpoint`, and `scan bom` render useful labels. |
| OSV federation | Query eligibility is intentional; unsupported refs do not false-query. |
| Matcher | Unsupported or non-version-like refs do not produce false findings. |
| Posture | Mutable/immutable install-reference behavior is intentional. |
| Fleet upload | Upload preparation preserves safe source identity and drops raw argv. |
| E2E | At least one realistic fixture proves the lifecycle across multiple sinks. |

## Pre-V0 setup (already done)

- Repo at `open-agent-security/openaca`.
- `LICENSE` (Apache-2.0).
- `README.md` (OSS positioning).
- `CLAUDE.md` (project conventions, OSS-only enforcement).
- `docs/specs/openaca-thesis.md` (thesis).
- Empty `docs/`, `schema/`, `advisories/`, `tools/` directories.

## Out of V0 (do not start until V0 ships)

- HTTP API.
- Benchmark harness.
- Public detection-rule format.
- Multi-platform CLI binary.
- Active disclosure pipeline at scale.
- `type: exposure` and `type: config` records.
- T3 (hash-based) advisories.
- Cursor / Windsurf manifest parsers.
