# ASVE V0 — Implementation Plan Index

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
| 007 | [fs-mode foundation: CLI split, attribution, claude-plugin matcher](007-fs-mode-cli-and-attribution.md) | 🟡 Active | 001, 003, 005 |
| 008 | [Component inventory: declared (repo) and active (fs) agent stack](008-component-inventory.md) | ⏸ Pending | 007 |

Status legend: 🟡 active · ✅ done · ⏸ pending · 🔴 blocked.

Keep at most **one** plan in 🟡 Active state at a time. Updating a plan's
status here is the source of truth for project progress alongside checkbox
state inside individual plans.

## Pre-V0 setup (already done)

- Repo at `open-agent-security/asve`.
- `LICENSE` (Apache-2.0).
- `README.md` (OSS positioning).
- `CLAUDE.md` (project conventions, OSS-only enforcement).
- `docs/specs/asve-v0-design.md` (canonical spec).
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
