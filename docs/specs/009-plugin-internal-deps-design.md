# Plan 009 Design — Plugin-internal implementation deps + OSV.dev federation

**Status:** Draft for review.
**Author:** brainstorming session 2026-05-10.
**Target plan:** `docs/plans/009-plugin-internal-deps.md` (to be written via `superpowers:writing-plans` after spec approval).

## Goal

Extend OpenACA scanning to Tier-2 — implementation dependencies inside active plugins (endpoint mode) and the host repo itself (repo mode) — while preserving the unique value-add empirically validated against `trivy filesystem ~/.claude/plugins/cache` and `osv-scanner --recursive ~/.claude/plugins/cache`: install-state filtering and per-plugin attribution.

Optional opt-in federation with OSV.dev's API gives users the option to combine OpenACA's filtering with OSV.dev's full generic-CVE corpus, delivering a better-UX alternative to existing recursive scanners for agent stacks specifically.

## Differentiators (validated 2026-05-10)

Trivy and osv-scanner walk filesystems blindly. Against `~/.claude/plugins/cache`, both produce noise that OpenACA removes by construction:

1. **Orphaned cache versions counted as live findings.** Both tools scanned `superpowers/5.0.7/...` and `superpowers/5.1.0/...`. Only the latter is active per `installed_plugins.json`. Anything in 5.0.7 is unreachable code — but trivy/osv would flag it as a current vulnerability.
2. **Test fixtures counted as runtime.** Both reported `superpowers/<version>/tests/brainstorm-server/package-lock.json`. That's a plugin-development test fixture, not a runtime path. Findings against it are false positives.
3. **No attribution.** Output is keyed on file path. The user can't tell which active plugin owns the vulnerable dep — making remediation guesswork.

OpenACA's `endpoint` mode walks per `installed_plugins.json` only, traverses plugin-declared/default paths (no `rglob` inside the install root, so `tests/` is skipped), and tags every emitted ref with `attributed_to = "claude-plugin/<name>@<version>"`. These properties propagate from `ComponentRef` to `Finding` to SARIF.

## Architecture

```
endpoint mode:
  parse_install
    ├─ _walk_active_plugins (per active plugin from installed_plugins.json)
    │    ├─ <existing> emit plugin self-identity
    │    ├─ <existing> _walk_plugin_install_root (Tier-1: MCPs/skills/hooks/commands/agents)
    │    └─ <new>      _walk_plugin_implementation_deps (Tier-2: lockfile or manifest fallback)
    └─ _walk_bare_components (unchanged)
  → (if --federate-osv) osv_federation.augment(refs, corpus) → merged corpus
  → match(refs, merged_corpus)
  → SARIF + verbose output

repo mode:
  parse_repo_grouped (REGISTRY expanded with package-lock.json + uv.lock)
  → (if --federate-osv) osv_federation.augment(refs, corpus)
  → match
  → SARIF + verbose output
```

### Component boundaries

| Module | Responsibility | Inputs | Outputs |
|---|---|---|---|
| `tools/parsers/package_lock_json.py` | Parse npm v3 `package-lock.json` | `Path` to lockfile | `list[ComponentRef]` with `ecosystem="npm"`, `extra={"transitive": True}`. Skips `""` (host) and `dev: true`. |
| `tools/parsers/uv_lock.py` | Parse `uv.lock` TOML | `Path` to lockfile | `list[ComponentRef]` with `ecosystem="PyPI"`, `extra={"transitive": True}`. Dev-vs-runtime filtering is best-effort. |
| `_walk_plugin_implementation_deps` (in `claude_install.py`) | Lockfile-first, manifest-fallback dispatch per ecosystem | `installPath`, `attributed_to` | `list[ComponentRef]` — lockfile refs `transitive=True`; manifest-fallback refs `transitive=False` with `fallback_reason`. |
| `tools/osv_federation.py` | OSV.dev API client + result merger | `list[ComponentRef]`, base corpus | Augmented corpus (base + OSV results, deduplicated by ID). Fail-soft on network errors. |
| `tools/sarif.py` extension | Surface `properties.{coverage, transitive, source}` per finding | `list[Finding]`, advisory index | Existing SARIF with new properties populated. |

Each module is independently testable: parsers take a Path and emit refs (pure file → data); the federation module is a function from refs + corpus → merged corpus (mockable HTTP); the dispatch is a pure function of the installPath state.

### Data flow: a finding's journey

```
package-lock.json
  → package_lock_json.parse(path)
  → ComponentRef(ecosystem=npm, name=lodash, version=4.17.20,
                 extra={transitive: True},
                 attributed_to="claude-plugin/superpowers@5.1.0")  // by _walk_plugin_implementation_deps

  (--federate-osv on)
  → osv_federation.augment([ref...], corpus)
    → POST /v1/querybatch with PURLs
    → for each vuln_id returned, fetch /v1/vulns/<id>
    → merge into corpus list; dedupe by id

  → match(refs, merged_corpus)
  → Finding(advisory_id, component=ref, attributed_to=ref.attributed_to,
            extra propagation TBD — see open question)

  → SARIF result with:
       properties.attributed_to = ref.attributed_to
       properties.coverage = "transitive" (from ref.extra)
       properties.transitive = True
       properties.source = "osv.dev" or "openaca.dev"
```

## Flags and defaults

| Flag | Default | Effect |
|---|---|---|
| (none — default scan) | — | Tier-1 + Tier-2 (lockfile/manifest); OpenACA corpus only. |
| `--exclude-transitive` | OFF (transitive included) | Skip `_walk_plugin_implementation_deps` entirely. Tier-1 still emitted. |
| `--federate-osv` | OFF (corpus-only) | Augment matching corpus with OSV.dev results for every emitted PURL. Findings tagged `source=osv.dev`. |

Both flags compose: `--exclude-transitive --federate-osv` would query OSV.dev only for Tier-1 refs that already have ecosystem+name+version (rare — most Tier-1 refs are identity-only for hooks/commands/agents). Almost a no-op, but mathematically consistent.

## SARIF property additions

New `properties` keys on each result (documented in `docs/sarif-conventions.md`):

| Key | Type | Values | When set |
|---|---|---|---|
| `attributed_to` | string \| null | `"claude-plugin/<name>@<version>"` | Existing from plan 007. Set when ref has attribution. |
| `coverage` | string | `"transitive"` \| `"direct-only"` | Tier-2 findings only. Omitted for Tier-1 inventory findings. |
| `transitive` | bool | `true` \| `false` | Mirror of `coverage` for easier downstream parsing. Omitted when `coverage` is omitted. |
| `source` | string | `"openaca.dev"` \| `"osv.dev"` | When `--federate-osv` is on. Default scans tag everything `"openaca.dev"`. |

`openaca.dev` parallels `osv.dev`'s naming (the eventual OpenACA domain) so consumers see consistent ecosystem-style provenance.

## Verbose output

Per-plugin coverage line added to the existing endpoint-mode `-v` block:

```
resolved 14 active plugin(s):
  claude-plugin/superpowers@5.1.0 (sha: 917e5f53) [scope=user]
    → 0 bundled MCPs, 14 bundled skills, 1 bundled hooks, 0 bundled commands, 0 bundled agents
    → npm: package-lock.json (transitive, 247 packages)
    → PyPI: no lockfile (direct only via pyproject.toml, 8 packages)
  ...
bare components: 5 skills
federation: osv.dev queried for 312 packages, 12 generic CVE findings added  // when --federate-osv
matched 14 finding(s):
  npm:lodash@4.17.20 → CVE-2021-23337 (high) via claude-plugin/superpowers@5.1.0 [source=osv.dev]
  ...
```

## Out of scope (V0)

- **yarn.lock, pnpm-lock.yaml, poetry.lock** — deferred until real plugins demand them; the original brainstorm called this out and pnpm-lock.yaml in particular has a non-trivial schema.
- **`node_modules` walking** — trust the lockfile or fall back to direct-deps-only.
- **Package-manager invocation** (npm/pip/uv resolution at scan time) — too slow, too fragile.
- **OSV.dev offline mirror** — `--federate-osv` queries live; no periodic cache job. Considered but adds storage and refresh discipline.
- **dev-vs-runtime filtering for `uv.lock`** — best-effort (over-report dev deps for V0). Refine in V1 if uv's schema stabilizes the distinction.
- **OSV.dev rate-limiting hardening** — V0 ships a simple batched POST. If rate limits bite, V1 adds retry/backoff.

## Trade-offs and alternatives considered

### Parse-all-lockfiles vs first-match priority

**Decision:** Parse every supported lockfile per active plugin.

A single plugin can legitimately ship JS code (with `package-lock.json`) alongside an embedded Python tool (with `uv.lock`). First-match priority would silently miss one ecosystem. Cost: one extra existence check per ecosystem per plugin — negligible.

### Lockfile vs manifest fallback semantics

**Decision:** Lockfile = full transitive. Manifest fallback = direct deps only. The two are NOT equivalent.

`extra["transitive"]` distinguishes them at the ref level; SARIF surfaces it as `properties.coverage`. Downstream consumers explicitly know which case they're in. Cost: one boolean per ref. Pretending the two are equivalent would let manifest-fallback findings claim full coverage they don't have.

### `--exclude-transitive` opt-out vs `--include-transitive` opt-in

**Decision:** Default ON, opt-out via `--exclude-transitive`.

Default-on mirrors Dependabot/Snyk default-everything behavior and matches the original brainstorm. Plan 008 dogfooding showed 14 active plugins; transitive deps may dominate findings volume, but that volume is real signal — the user invoked an SCA scanner, they should see SCA results. Power users wanting agent-stack-only output add `--exclude-transitive`.

### OSV.dev federation: live query vs offline mirror

**Decision:** Live query at scan time via `--federate-osv`.

Live: simple (~150 LOC for client + merger), no storage, always-fresh. Cost: network dependency. Fail-soft means scans still work offline.

Offline mirror: bigger scope (storage, refresh cadence, OSV-format compatibility for ~30k records). Deferred to V1.

### OSV.dev integration: opt-in vs default-on

**Decision:** Opt-in via `--federate-osv`.

Default-off keeps the default OpenACA experience focused on the agent-stack corpus and avoids hidden network calls. Users wanting full Tier-2 coverage explicitly opt in. Documents the value-add but doesn't force it.

### Active-state filtering as OpenACA's edge

**Decision:** OpenACA's `endpoint` mode walks per `installed_plugins.json` and per plugin.json defaults — not via filesystem `rglob`.

This is what makes OpenACA more accurate than trivy/osv-scanner for the agent stack. Without it, federation would still be useful (the corpus is bigger) but without filtering, OpenACA wouldn't offer a UX advantage over `osv-scanner --recursive`. Active-state filtering is the load-bearing differentiator.

## Resolved details

1. **`Finding.extra` propagation.** `Finding` stays minimal: only `attributed_to` (mirror from plan 007) plus `advisory_id`, `confidence`, `component`, `reason`. `coverage` / `transitive` / `source` live in `ComponentRef.extra` and on the advisory record; the SARIF emitter dereferences from both. Rationale: `coverage`/`transitive` are pure ref properties (no per-finding override case), and `source` is a pure advisory property — the plan 007 rationale for mirroring `attributed_to` (per-finding override + immutability) doesn't extend.

2. **OSV.dev batch query: single pass, no in-process cache.** Collect every emitted PURL into one set, chunk into batches of ≤1000 packages, POST to `/v1/querybatch`, fetch full vulnerability records via `/v1/vulns/<id>`. OSV.dev deduplicates the batch on its end; no cache layer needed. Simpler code; fewer round trips.

3. **Network failure UX.**
   - **Exit code stays findings-driven.** Network failure is a coverage gap, not a finding. Exit code reflects `--fail-on` semantics only.
   - **Unconditional stderr warning when federation fails.** If the user passed `--federate-osv` and the network call fails (timeout, non-200, malformed response), print a one-line warning to stderr regardless of `-v`. The user explicitly opted in; silent fallback to corpus-only violates principle of least surprise.

## Acceptance criteria

- `openaca scan endpoint --config-dir ~/.claude --advisories advisories -v` shows per-plugin Tier-2 coverage lines.
- `--exclude-transitive` suppresses lockfile/manifest walks; Tier-1 unchanged.
- `--federate-osv` queries OSV.dev for emitted PURLs; merged corpus produces additional findings with `source=osv.dev`; fail-soft on network errors.
- Repo mode lockfile scanning works on a host repo's `package-lock.json` and `uv.lock` at root.
- SARIF carries `properties.coverage`, `properties.transitive`, `properties.source` on Tier-2 findings.
- Active-state filtering remains: orphaned cache versions never produce findings; `tests/` subdirs inside plugins are never walked.
- 280+ tests pass; ruff format/check clean; pyright clean; `openaca lint advisories/` clean.
- Dogfood: real `~/.claude` scan produces sensible output (active plugins with coverage lines, optional federation findings).
