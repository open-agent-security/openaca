---
id: 0008
title: Lockfile dispatch; manifest fallback; OSV.dev federation as opt-in
status: superseded
date: 2026-05-10
supersedes: null
superseded-by: 0009
---

## Context

Plan 007 wired identity + attribution. Plan 008 enumerated the Tier-1
declarative agent stack. Plan 009 adds Tier-2 implementation-deps SCA —
the dominant attack-surface for known-CVE matching. Empirical dogfood
(2026-05-10) against `~/.claude/plugins/cache` showed that
`trivy filesystem` and `osv-scanner --recursive` both walk orphaned
cache versions and plugin test fixtures, with no plugin attribution
on results. Plan 009 differentiates by being install-state-aware and
attribution-aware. The design choices below would be re-suggested
without an ADR.

## Decision

### 1. Parse ALL supported lockfiles per active plugin, not first-match

A single plugin can legitimately ship JS code (with `package-lock.json`)
alongside an embedded Python tool (with `uv.lock`). First-match priority
would silently miss one ecosystem. Cost: one extra existence check per
ecosystem per plugin — negligible.

### 2. Lockfile vs manifest fallback are NOT equivalent

Lockfile = full transitive tree for that ecosystem. Manifest fallback =
direct deps only. Manifest-fallback emissions tag `extra["transitive"]
=False` and `extra["fallback_reason"]=f"no {ecosystem} lockfile present"`.
SARIF surfaces this via `properties.coverage`. Downstream consumers
explicitly know which case they're in. Pretending the two are equivalent
would let manifest-fallback findings claim coverage they don't have.

### 3. `--exclude-transitive` is opt-OUT (default OFF)

Default-on mirrors Dependabot/Snyk/Trivy default-everything behavior.
Power users wanting agent-stack-only output disable via the flag.
Considered alternative: default-off (opt-in via `--include-transitive`)
— rejected because the dominant CVE-matching use case is Tier-2, and
making it opt-in would surprise users coming from traditional SCA.

### 4. `--federate-osv` is opt-IN (default OFF)

OSV.dev federation adds a network dependency to scans. Default-off
keeps the default scan offline and focused on the OpenACA corpus. Users
who want full Tier-2 coverage (generic CVEs in plugin transitive deps)
explicitly opt in. Considered alternatives:

- **Default-on federation**: makes scans network-dependent by default;
  rejected as too aggressive for V0.
- **Offline OSV.dev mirror**: ~30k records, refresh discipline,
  significant storage. Deferred to V1.

### 5. OpenACA's value-add is filtering + attribution, not corpus coverage

The empirical comparison against `trivy`/`osv-scanner` showed they
report against orphaned cache versions and test fixtures inside plugins
with no attribution. OpenACA walks per `installed_plugins.json` (active
plugins only) and per `plugin.json` defaults (no `rglob` inside the
install path), tagging every Tier-2 ref with `attributed_to`. This is
the load-bearing differentiator — federation enhances it; it doesn't
replace it.

### 6. No `node_modules` walking, no package-manager invocation

Trust the lockfile or fall back to direct-only manifest scanning.
Re-implementing npm/pip resolution at scan time is V0-out-of-scope.

### 7. `uv.lock` dev-vs-runtime filtering is best-effort

`uv.lock` doesn't reliably annotate dev-only the way npm's `dev: true`
does. V0 emits all packages from `uv.lock`; over-reporting dev deps is
acceptable. Refine in V1 if uv's lockfile schema stabilizes the
distinction.

### 8. `source` ecosystem-style naming: `openaca.dev` and `osv.dev`

Per-finding SARIF property `properties.source` takes values `"openaca.dev"`
or `"osv.dev"` (matching the OSV.dev convention). Future-aligned with
the eventual openaca.dev domain; consistent ecosystem-style provenance
for downstream consumers.

## Alternatives considered

- **First-match lockfile priority**: rejected (multi-language plugins
  miss ecosystems).
- **Treat manifest fallback as full coverage**: rejected (claims coverage
  it doesn't have).
- **Default-on federation**: rejected (network-dependent default scan).
- **Offline OSV.dev mirror**: deferred to V1 (storage + refresh
  discipline beyond V0 scope).
- **Reimplement package-manager resolution**: rejected (V0-out-of-scope).

## Consequences

**Enables:**
- OpenACA becomes a better-UX Tier-2 scanner than `trivy`/`osv-scanner` for
  the agent-stack case (filtered + attributed).
- `--federate-osv` lets users compose OpenACA's filtering with OSV.dev's
  full corpus.
- Lockfile-vs-manifest coverage is honestly surfaced in SARIF.

**Costs:**
- `--federate-osv` adds a network dependency when enabled. Fail-soft
  semantics mitigate (warning + continue with corpus-only).
- `uv.lock` over-reports dev deps in V0.
- Two new flags add CLI surface area.

**Watch:**
- OSV.dev rate-limiting if scans grow large; V1 may need backoff/retry.
- If yarn.lock or pnpm-lock.yaml become demand-driven (real plugins
  using them), add parsers in a follow-up.

## When to revisit

- We add an offline OSV.dev mirror (V1+).
- A real plugin ships in yarn.lock or pnpm-lock.yaml format.
- uv's lockfile schema stabilizes dev-vs-runtime annotations.
- We hit OSV.dev rate limits on real scans.
