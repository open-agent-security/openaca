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

### 1. Two scan modes via Click subcommands; subcommand required

`asve-scan repo --target <repo>` is the manifest-walk mode (today's
behavior factored into an explicit subcommand). `asve-scan endpoint` is
the install-state-aware endpoint mode introduced in plan 007 (and extended
by plans 008 and 009), following the Claude Code install model:
`settings.json → installed_plugins.json → plugin install paths`.

Endpoint mode reads a Claude Code config directory from `--config-dir`.
When omitted, it defaults to `$CLAUDE_CONFIG_DIR` if set, else `~/.claude`.
Project settings are opt-in through `--project <repo>`, which layers that
repo's `.claude/settings.json` and `.claude/settings.local.json` on top of
the endpoint config.

A subcommand is **required** — there is no no-subcommand fallback. ASVE is
pre-V0-launch with no external consumers depending on the CLI shape, so
back-compat hedging would cost code clarity for zero benefit. The
GitHub Action's `action.yml` invokes `asve-scan repo` explicitly. After V0
public launch the CLI surface becomes a contract; until then, change it
freely if a redesign is cleaner.

The endpoint subcommand was originally named `fs`, mirroring generic
filesystem scanners. That name was rejected pre-launch: this mode is not a
blind recursive filesystem walk, and it should not infer "project target"
from a repo-shaped path. Naming it `endpoint` makes the scan target explicit:
the installed agent stack on a developer machine, CI runner, or similar
host.

Shared options (`--sarif`, `--fail-on`, `-v`) can be placed before or after
the subcommand for ergonomic invocation (`asve-scan -v repo --target X`
or `asve-scan repo --target X -v` are equivalent). Repo mode requires
`--target`; endpoint mode has optional `--config-dir` plus optional
`--project`.

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
- The CLI surface grows by two subcommands; either `repo` or `endpoint` is required.
  The GitHub Action's `action.yml` was updated to invoke `asve-scan repo`
  explicitly. (This Consequences bullet originally claimed a no-subcommand
  default preserved back-compat — that fallback was removed before any
  external consumer existed. See the Decision section above for the
  authoritative current behavior; this bullet is corrected to match.)
- All findings now carry an attribution slot, populated or not. Output
  rendering checks for None and elides the `via ...` suffix when absent.
- Plans 008 and 009 build directly on this foundation: plan 008 walks
  active plugin install roots and tags every emitted ref's
  `attributed_to`; plan 009 extends the same mechanism to
  lockfile-resolved transitive deps.
