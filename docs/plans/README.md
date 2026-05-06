# ASVE V0 — Implementation Plan Index

V0 implementation is split into six plans. Each produces working, testable
software on its own. Run them in order; later plans depend on earlier ones
unless noted.

| # | Plan | Status | Depends on |
|---|---|---|---|
| 001 | [Schema + advisory-authoring tooling](001-schema-and-tooling.md) | 🟡 Active | — |
| 002 | [First V0 advisories + OSV importer](002-first-advisories.md) | ⏸ Pending | 001 |
| 003 | [Manifest parsers](003-manifest-parsers.md) | ⏸ Pending | 001 (project setup) |
| 004 | [Static export pipeline](004-static-export.md) | ⏸ Pending | 001, 002 |
| 005 | [Reference GitHub Action](005-reference-action.md) | ⏸ Pending | 003, 004 |
| 006 | [Disclosure policy doc](006-disclosure-policy.md) | ⏸ Pending | — |

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
