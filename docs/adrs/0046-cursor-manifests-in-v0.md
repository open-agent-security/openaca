---
id: 0046
title: Cursor manifests are V0 scope, active by default
status: accepted
date: 2026-08-20
supersedes: null
superseded-by: null
---

## Context

`CLAUDE.md`'s "V0 scope" section (item 2) enumerated the V0 manifest
parsers and closed with "Cursor/Windsurf manifests are V1"; the thesis's
V0 → V1 table carried the same line as its "V1 manifest coverage" row.
Both predate the host abstraction. ADR-0044 then introduced
`HostAdapter` and the host-tagged registries, and ADR-0045 onboarded
Cursor across every scan surface — with repo mode defaulting `--host` to
every registered host, pinned by `test_scan_repo_default_host_includes_cursor`.

A Codex review on PR #158 caught the contradiction: a plain
`openaca scan repo` now parses `.cursor/mcp.json` and the rest of
Cursor's surfaces with no opt-in flag, while the governing scope
document still reserved those parsers for V1. Neither ADR-0044 nor
ADR-0045 addressed the product-level V0/V1 line, so the code and the
release contract disagreed with no record resolving which was right.
(`docs/specs/multi-host-support.md` uses "V1" for a feature-local
milestone — Rules, `AGENTS.md`-as-Cursor-convention-file, Extensions —
which is unrelated to this product-level gate.) This ADR is that record.

## Decision

Cursor manifest parsers are **V0 scope and active by default**. The V0
scope line in `CLAUDE.md` and the thesis's V1-manifest-coverage row are
amended accordingly: Cursor moves into the V0 parser set, Windsurf and
ChatGPT-style plugin manifests remain V1. Repo mode continues to select
every registered host when `--host` is omitted; Cursor coverage is not
gated behind an opt-in flag. The V0/V1 boundary for manifest coverage is
now "which hosts are registered in `tools/hosts.py`", not a frozen
filename list — a host lands in V0 when its adapter, parsers, posture
rules, and tests ship, recorded by its own ADR.

## Alternatives considered

- **Gate Cursor behind an explicit `--host cursor`, leaving the default
  scan Claude-Code-only** (Codex's suggested fix) — rejected. It
  preserves a documentation line by degrading the product: users on
  Cursor repos would get a silently empty scan unless they knew to pass
  a flag, which is the failure mode host-agnostic scanning exists to
  remove. It also contradicts ADR-0044's deliberate mode-asymmetric
  `--host` default (repo = every registered host, machine-state
  independent), so honoring it would mean reopening an accepted ADR to
  satisfy a stale sentence.
- **Leave both documents unchanged and treat the scope line as
  self-evidently superseded by ADR-0044/0045** — rejected. Neither ADR
  mentions the product-level roadmap, so nothing in the tree actually
  said the line had moved; the next reviewer (human or agent) would
  re-raise the identical finding. An unwritten amendment is not an
  amendment.
- **Amend ADR-0009 (overlay-only V0), which the scope section points
  to** — rejected as a no-op: ADR-0009 is about overlay-only record
  shape and says nothing about manifest parsers or host coverage, so
  there is nothing there to amend or supersede.
- **Freeze the V0 parser list as an exhaustive filename enumeration and
  require a scope amendment per host** — rejected. Every future host
  (Codex, Copilot) would need a governance edit in lockstep with its
  implementation ADR, and the enumeration had already drifted from the
  code once (the Plan 008/009 skill, command, and lockfile parsers were
  never added to it). Tying the boundary to the host registry keeps one
  source of truth.

## Consequences

Cursor repos get findings from a default `openaca scan repo` with no
flag. The V0 release contract now describes what the scanner actually
does, and the next host's implementer inherits a clear rule: register
the host, write its ADR, and V0 coverage follows — no separate scope
negotiation. The cost is that "V0" no longer names a fixed manifest set,
so the `CLAUDE.md` enumeration is illustrative of the registered hosts
rather than normative; if it drifts again, `tools/hosts.py` and the
registries in `tools/parsers/__init__.py` win. Windsurf and
ChatGPT-style manifests stay out until they have adapters and ADRs of
their own, so the V1 row is not empty.

## When to revisit

If a host is ever onboarded that should ship *behind* a flag — an
experimental adapter, or one whose parsers are known to produce false
positives on other hosts' repos — that host needs an opt-in default and
this ADR needs superseding. Also revisit if `--host`'s repo-mode default
changes for any reason (that default is load-bearing for this
decision's "active by default" half).
