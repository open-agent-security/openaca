---
id: 0004
title: Year in ASVE-YYYY-NNNN is assignment year, not alias year
status: accepted
date: 2026-05-08
supersedes: null
superseded-by: null
---

## Context

ASVE advisories carry IDs of the form `ASVE-YYYY-NNNN` and frequently
alias upstream CVE/GHSA records whose own year component reflects a
different timeline. The first batch of V0 advisories (`ASVE-2026-0001`
through `ASVE-2026-0005`) all alias CVE-2025-* IDs, which prompted a
reasonable question: should ASVE match the upstream year to avoid
visual confusion?

## Decision

The `YYYY` component of an ASVE ID is the year **ASVE assigned the
ID**, not the year of any aliased upstream record. ASVE-2026-0001 means
"ASVE catalogued this in 2026" — the aliased CVE-2025-XXXXX year is
independent and reflects MITRE's assignment timeline.

This matches CVE, GHSA, RUSTSEC, PYSEC, and every comparable
advisory database. ID reservation (`tools/reserve_id.py`) takes the
next free number in the **current calendar year** at authoring time.

## Alternatives considered

- **Match the aliased upstream year**: at first glance reduces
  visual mismatch (`ASVE-2025-0001` aliasing `CVE-2025-XXXXX`).
  Rejected because:
  - Multi-alias records have no single "right" year (a CVE-2024 and
    a GHSA-2025 alias on the same advisory force a coin flip).
  - ASVE-original records (no upstream alias) need *some* rule, and
    that rule will diverge from the alias-year rule, creating a
    two-mode policy.
  - Reservation gets harder: claiming numbers in past-year
    namespaces creates non-monotonic reservation order and gaps.
  - Diverges from every other advisory DB's convention; downstream
    consumers used to "year = assignment year" will misread it.

- **Drop the year entirely (`ASVE-NNNNNN`)**: cleaner, no apparent
  contradiction. Rejected because OSV-shaped consumers expect the
  `PREFIX-YYYY-N+` shape, and the year is useful at-a-glance
  triage signal ("how old is this catalogue entry?").

## Consequences

- Visual mismatch between `ASVE-YYYY` and aliased `CVE-YYYY`/`GHSA-YYYY`
  is permanent and expected. Worth a one-line note in `CONTRIBUTING.md`
  when that file lands so contributors don't refile this question.
- Reservation logic stays simple: max + 1 in current year.
- Backfilling old vulnerabilities into ASVE in 2027 will produce
  `ASVE-2027-NNNN` IDs, which is correct under this convention even
  though the underlying flaw may date to 2024.

## When to revisit

If a downstream consumer requires alias-year alignment as a hard
constraint (no current evidence this exists), or if ASVE ever issues
IDs at scale ahead of cataloging — i.e., decoupling reservation from
authoring such that the assignment year stops meaningfully reflecting
"when ASVE recorded this." Neither is on the roadmap.
