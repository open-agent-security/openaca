# Plan 047 — Pi agent kind

Surface contract: [Pi Agent Kind](../specs/pi-agent-kind.md).
Architecture: [ADR-0067](../adrs/0067-pi-agent-kind.md).

## Goal and boundary

Register `pi` for repository and endpoint composition, covering configured packages,
extensions, skills, prompts and themes. Report mutable package references and the
permissive global project-trust default. Anchor behavior to Pi 0.85.1.

MCP adapters, dynamic extension execution, invocation-only resources and overrides,
and transitive npm dependency inventory are outside this release. npm source identity
permits advisory matching when version evidence exists; it does not imply complete
advisory coverage. The scanner never installs or executes Pi packages.

## Ground truth

Verified against Pi 0.85.1's shipped `dist/core/package-manager.js`,
`dist/core/trust-manager.js`, `dist/core/project-trust.js`, `dist/utils/git.js`, and
[versioned documentation](https://github.com/earendil-works/pi/tree/v0.85.1/packages/coding-agent/docs).

- Global root defaults to `~/.pi/agent`, with `PI_CODING_AGENT_DIR` relocating it.
  Project settings live at `.pi/settings.json`. Shared `.agents/skills` has its own
  global and project ancestor discovery surface.
- `packages` accepts strings or objects. The other four resource arrays contain
  local paths and filter strings. Native resource directories also auto-discover
  when settings are absent.
- Package manifests use the `pi` object in `package.json`, with conventional
  resource directories as fallback where Pi applies them.
- Only `npm:` sources are npm in the shipped parser; bare names are local paths,
  despite conflicting examples in the settings documentation.
- Package identity is `npm:<name>`, `git:<host>/<path>` or `local:<absolute-lexical-path>`.
  SSH and HTTPS spellings of one Git repository deduplicate. Relative local paths
  resolve against the declaring settings directory without following symlinks for source identity; canonical paths enforce read boundaries and final file deduplication.
- Project packages normally replace the corresponding global package. A project
  `autoload: false` entry instead applies explicit per-file enable overrides and
  retains unmentioned global resources. An empty delta changes nothing. Omitted
  regular filters and empty regular filters are different.
- `trust.json` maps canonical directories to booleans or null. The nearest
  ancestor boolean wins. Without one, `always` trusts, `never` refuses, and `ask`
  leaves interactive behavior unresolved (non-interactive Pi refuses). CLI and
  extension trust overrides are not observable from static configuration.

## Architecture and invariants

ADR-0044 supplies kind registration and one BOM per agent. ADR-0042 separates
occurrence identity, stable source identity and matching coordinates. ADR-0053
permits a distinct composer when traversal differs, while reusing graph primitives.
ADR-0065 requires graph-derived posture to follow selected composition.

- Parse sources without importing posture or modifying the global reference checker.
  Interpret HTTPS as Git only inside a Pi package declaration.
- Preserve configured source separately from observed installed version. A resolved
  version never converts an unpinned declaration into a pinned one. Git tags remain
  mutable under OpenACA's definition; a full commit SHA is immutable.
- Normalize settings, expand resources, and construct graphs through separate focused
  modules. Retain declaration provenance and owning package for each selected file.
- Repository scans read declared resources within the target boundary. They do not
  consult the invoking user's global settings or trust. Missing/external resources
  retain declaration evidence and produce explicit coverage gaps.
- Installed composition includes project resources only when persisted configuration
  establishes trust. Unresolved/refused project resources do not become effective
  components. Composition-source is an agent-level property, not a per-file toggle.
- Mutable-reference posture uses selected package refs. Global-default trust posture
  reads global settings through the existing extra-manifest channel. No MCP rules
  or duplicate raw-package posture scan are registered for Pi.
- Keep existing Codex trust findings and non-Pi reference classification unchanged.
- Collection wiring uses `tools/collect.py`; no hosted-service client is introduced.

## Implementation

- [x] **1. Source normalization:** npm, hosted/Git/SSH/HTTPS sources, local paths,
  canonical dedup identity, pin classification and supported PURLs.
  Verified source behavior against shipped Pi 0.85.1; focused tests and task review passed.
- [x] **2. Resource resolution:** native discovery, package manifests/conventions,
  filters, precedence, deltas, local paths, shared skills and filesystem boundaries.
- [x] **3. Trust resolution:** real boolean/null store, nearest ancestors, fallback
  classification and malformed-store coverage.
- [x] **4. Installed-version evidence:** source-scoped npm/Git install roots,
  independently observed version, missing/stale-install handling.
- [x] **5. Posture adaptation:** selected Pi package references through existing
  mutable-reference rule; accurate global-default trust finding and collector.
- [x] **6. Kind and graph integration:** discovery, declared/installed composition,
  registry, parser accounting, graph identity and BOM consumer compatibility.
- [x] **7. CLI and documentation:** scan/BOM/collection paths, end-to-end fixtures,
  coverage and operational limitations, complete verification.

Tasks 2 and 4 share their data model and are implemented together. Other tasks
proceed through focused tests and task review before downstream integration.

## Acceptance

- [x] Source tests cover exact versions, ranges, tags, full SHAs, hosted shorthand,
  unpinned URLs, local paths, and unpinned sources with observed versions.
- [x] Resource tests cover all five arrays, defaults without settings, package
  manifests and conventions, exclusions, empty/omitted filters and project deltas.
- [x] Unresolved project trust never makes project resources installed components.
- [x] Configured source and observed version survive BOM export independently.
- [x] Findings attach to the selected package occurrence; shadowed declarations
  do not produce duplicate mutable-reference findings.
- [x] Declared scans respect gitignore and target containment; installed scans
  cannot silently combine a foreign config root with the scanner user's home.
- [x] BOM export, lint, round-trip and collection consumers accept Pi data.
- [x] CLI fixtures demonstrate both composition and posture; documentation states
  the MCP, dynamic execution and dependency coverage limits.
- [x] Ruff, formatting, Pyright and the full pytest suite pass (2333 tests).
- [x] Clean-archive pre-push gate passes on the final combined implementation commit.
- [x] Final branch review passes, including the skill-frontmatter review correction.

## Delivery verification

Task 7 exercises real Click repository/endpoint scans and BOM export with a local
mock advisory source, including npm matching with an observed version, mutable
configured sources, unresolved project trust, and all five Pi component types.
Endpoint units count selected resources rather than package containers, including
standalone/shared resources and excluding disabled files; existing kind counts
are unchanged. The published installed collection facade produces the same units.
BOM lint and CLI advisory replay pass. The existing `scan bom` posture prohibition
is preserved; the posture runner separately recomputes the mutable finding from
round-tripped refs. No package execution, install, or real-home writes are used.

The frontmatter correction is included in `2545ba2`, whose clean-archive pre-push
gate passes all 2333 tests, lint, formatting, types, corpus validation and CLI
smoke checks. Final branch review passed, including 111 independently run
Pi-focused tests. Native policy compilation
remains scoped to its existing Claude target; this plan adds no Pi policy writer.
No remote push or PR is part of this local implementation handoff.

## Review hardening

- [x] Reproduce native resource/package classification across settings presence,
  package repositories, extension entry points, and shared skills.
- [x] Classify native declarations before package construction and registry
  accounting; retain explicit package selection (ADR-0068).
- [x] Normalize every ancestor shared-skill root; compare exported occurrence keys
  across independent homes, with and without a Git boundary.
- [x] Exercise native extension inventory through CLI scan, BOM lint, and graph
  round-trip, without fabricating an npm package advisory target.
- [x] Verify review fixes with the full test suite (2359 tests), Ruff, and Pyright.
