---
id: 0006
title: asve-scan subcommands; claude-plugin ecosystem; attributed_to fields
status: accepted
date: 2026-05-10
supersedes: null
superseded-by: null
---

## Context

V0 ships `asve-scan` as a single-command repo-manifest scanner. Pointing it
at an installed Claude Code tree (`~/.claude/`) produces noisy and
misattributed output: `rglob("package.json")` walks every cached plugin's
internals and emits its transitive npm deps as if the user installed them
directly. Plugin advisories also fail to match — parsers detect
`.claude-plugin/plugin.json`, but the matcher's `_match_one` only knows two
identity prefixes (`mcp-stdio/...-unpinned:`).

This ADR captures three coupled decisions made together because plans 008
and 009 build on the data model established here. They concern: how the
CLI exposes the two scan modes, how plugin advisories match, and how
"discovered via active plugin X" attribution flows through the
ComponentRef → Finding → SARIF chain.

## Decision

### 1. Two scan modes via Click subcommands; back-compat default to `repo`

`asve-scan repo <target>` keeps today's manifest-walk behavior.
`asve-scan fs <target>` is the install-state-aware mode introduced in plan
007 (and extended by plans 008 and 009). It follows the canonical Claude
Code install model: `settings.json → installed_plugins.json → plugin
install paths`.

`asve-scan <flags>` (no subcommand) defaults to `repo` for back-compat with
the GitHub Action and existing scripts. The default is documented (in this
ADR and in README), not silent magic — group-level options at the CLI are
optional fallbacks; subcommand-level options are required.

Trivy uses the same `repo` / `fs` / `image` split for the same reason: scan
modes have different invariants (e.g., what to recurse into) and naming
the mode prevents pointing-at-the-wrong-layer mistakes that produce
misattributed output.

### 2. `claude-plugin` is a recognized ecosystem in `affected[*].package`

Plugin advisories use `affected[*].package.ecosystem: "claude-plugin"` and
`name: <plugin-name-from-plugin.json>`. The parser tags self-identity refs
with `ecosystem="claude-plugin"`, `name`, `version` *in addition to* the
existing `component_identity` string. The matcher's existing
`_match_versioned` path then handles range matching identically to npm or
PyPI — no matcher logic change required.

**Alternative considered and rejected**: store plugin identity in
`database_specific.asve.component_identity` and add a parallel matching
path keyed off the prefix. Rejected because it duplicates the OSV ECOSYSTEM
range-matching machinery for no semantic gain. OSV ecosystem strings are
open vocabulary by design; using one matches the existing schema shape.

OSV-Scanner consumers may not recognize a custom `claude-plugin` ecosystem
and will likely skip those records. That's a known propagation gap, not a
blocker for ASVE-native consumption (the reference Action and the static
export both work fine). When a third party wants plugin coverage, they
adopt the same ecosystem string.

### 3. `attributed_to` mirrored on ComponentRef and Finding

Attribution is the relationship "this component was discovered via active
plugin X." Two fields, populated symmetrically:

- `ComponentRef.attributed_to: Optional[str]` — set by parsers at emission
  time. Default `None`. The plan-007 minimal `claude_install` resolver sets
  it on bundled refs (none yet, since plan 007 only emits one ref per
  active plugin); plans 008 and 009 populate it more broadly.
- `Finding.attributed_to: Optional[str]` — copied from
  `finding.component.attributed_to` when the matcher constructs a Finding.
  Mirroring (rather than dereferencing through the component) gives output
  code clean `finding.attributed_to` access and lets the matcher override
  per-finding in the future without breaking ComponentRef immutability
  assumptions.

Surface points:

- Text scan output: findings get a `via <attributed_to>` suffix when set.
- GitHub workflow annotations: appended to message before emitting.
- SARIF v2.1.0: `properties.attributed_to` per result. Properties block
  omitted when attribution is None to keep direct-finding output tight.

## Consequences

- The `claude-plugin` ecosystem string becomes part of the corpus contract.
  Future advisories targeting plugins use it. CONTRIBUTING.md ecosystem
  list documents it.
- The CLI surface grows by two subcommands. Back-compat is preserved by the
  no-subcommand default; the GitHub Action's `action.yml` doesn't need to
  change.
- All findings now carry an attribution slot, populated or not. Output
  rendering checks for None and elides the `via ...` suffix when absent.
- Plans 008 and 009 build directly on this foundation: plan 008 walks
  active plugin install roots and tags every emitted ref's
  `attributed_to`; plan 009 extends the same mechanism to
  lockfile-resolved transitive deps.
