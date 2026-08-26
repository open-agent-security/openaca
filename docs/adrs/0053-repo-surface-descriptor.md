---
id: 0053
title: Parameterize repo-mode composition by surface descriptor; fork endpoint seeding per kind
status: accepted
date: 2026-08-25
supersedes: null
superseded-by: null
---

## Context

ADR-0044 gave each agent kind a `compose` callable that owns graph construction,
and ADR-0052 registers a second kind. That forces a question the first kind never
had to answer: how much of `tools/graph_build.py` is *about Claude Code*, and how
much is about building a graph at all.

The module has two entry paths and they are not alike.

**Repo mode** — `descend`, `_find_plugin_roots`, `_add_project_skills`,
`_add_repo_standalone_components`, `_is_claude_settings_json`,
`_command_agent_kind`, `_add_bundled_plugin_surfaces` — is Claude-specific only
in its **string literals**: `.claude`, `.claude-plugin`, `.mcp.json`, and two
module-level tables. Everything structural around them is kind-neutral and
represents a long tail of already-fixed bugs: two-root gitignore threading
(`_ignore_context`, `_is_ignored_under`), the `exclude_under` discipline that
keeps the graph single-parent, symlink containment, `_add_dep_manifest_packages`,
ADR-0039 launch-dependency attachment, the normalizer and `occurrence_key`
contract, and `_add_child`'s identity finalization. That is roughly 600 lines
that a second kind needs verbatim.

**Endpoint mode** — `_seed_endpoint` and its subtree — is Claude-specific in its
**control flow**: settings-layer precedence, `claude_install`,
`installed_plugins.json ∩ enabledPlugins`, tier-2 lockfile dependency walking.
Cursor has no analogue for any of it. It has no install lockfile, no settings
layers, and (per ADR-0052) no readable enable state at all.

Treating both halves the same way is the mistake available here, in either
direction: forking repo mode duplicates 600 lines of hard-won correctness, and
parameterizing endpoint mode produces a strategy object with roughly ten optional
callbacks, which is a fork wearing indirection.

## Decision

**Repo mode is parameterized. Endpoint mode is forked.**

A new module `tools/repo_surface.py` holds frozen descriptors — `RepoSurface`,
`PluginFormat`, `BundledLayout` — naming the directories, filenames, manifest
formats, and shadowing rules a kind reads in a repository tree. `graph_build`'s
repo-mode helpers take `surface: RepoSurface = CLAUDE_CODE_SURFACE`, whose values
are transcribed verbatim from today's literals.

Endpoint seeding forks: each kind that supports the installed source brings its
own seed function, reached through its `compose`. `build_rooted_graph`'s endpoint
branch ignores `surface` entirely, and `_seed_endpoint` remains Claude Code's,
unchanged in behavior and in docstring.

Shared construction helpers a second kind's composer needs are **published under
public aliases** rather than imported as underscore-prefixed privates across
module boundaries. Renaming one of them is an interface change, not a refactor.

`tools/repo_surface.py` imports only parser leaves. `graph_build` imports
`repo_surface`. Kind modules import both, lazily. **`graph_build` still never
imports `agent_kinds`** — the one-way dependency ADR-0044 established is
preserved.

The parameterization lands as its own change with **zero entries for any second
kind**, so the existing Claude Code test suite is an uncontaminated regression
gate. A golden-graph assertion pins a fixture repo's graph byte-identical across
the change.

## Alternatives considered

- **Fork repo mode too — a parallel walker per kind** — rejected because it
  duplicates the gitignore threading, `exclude_under` single-parent discipline,
  symlink containment, dependency-manifest attachment, and launch-dependency
  resolution. Each of those encodes fixed bugs, and a fork re-earns every one of
  them on a schedule nobody controls. It is the lower-risk option for exactly one
  release and the higher-risk option thereafter.
- **Parameterize endpoint mode with the same descriptor** — rejected because the
  two runtimes' installed composition differs in control flow, not vocabulary. A
  descriptor expressive enough for both would carry a callback per phase, at
  which point the descriptor is doing nothing the fork does not do more legibly,
  while putting Claude Code's shipped seeding behavior at risk.
- **Put the descriptor in `graph_build` itself** — rejected because kind modules
  need the constants, and importing them from `graph_build` while `graph_build`
  builds their graphs invites the cycle the neutral module avoids.
- **Put the descriptor under `tools/agent_kinds/`** — rejected because it is
  graph-construction data, and locating it there invites `agent_kinds` gaining a
  module-level `graph_build` import, breaking the one-way rule.
- **Import `_add_child`, `descend`, and friends directly as privates** — rejected
  because a cross-module private import is an interface with no declaration: the
  next person to rename one has no signal that a second kind depends on it.
- **Land parameterization and the second kind together** — rejected because the
  regression gate stops being a gate. With no second-kind entries in the diff,
  any change in Claude Code's output is unambiguously a defect in the
  refactor; mixed together, it is an argument.
- **Extend `_registry_pattern_matches` with more hand-rolled cases** — rejected
  as a related sub-decision. That function already special-cases four
  `.claude`-shaped globs by hand; a second kind doubles it into a table of
  near-identical loops. `pathspec` is already a dependency and already used for
  the gitignore walk, so compiled git-wildmatch patterns replace the special
  cases — behavior-preserving, given the two slash-anchored patterns are rewritten
  with a `**/` prefix to keep their at-any-depth meaning, since git anchors
  slashed patterns at the root and the hand-rolled code did not.

## Consequences

**Enables.** A third kind adds *data* — a `RepoSurface` constant — rather than a
walker. The repo-mode bug surface stays single: a containment or gitignore fix
lands once and every kind gets it.

**Costs.** `descend` and six helpers gain a parameter they did not have, and the
descriptor is a second place to look when asking why a file was or was not
picked up. `PluginFormat` in particular encodes a non-obvious rule — Cursor
resolves plugin manifests through an *ordered candidate list* where the first
**qualifying** candidate wins, and a candidate that parses but declares neither
components nor metadata falls through — so precedence alone does not capture it
and the qualification test is part of the format, not the caller.

The split means one module (`graph_build`) is parameterized while its sibling
seeding path is forked. That asymmetry looks like an inconsistency to a reader
who has not read this ADR, and someone will eventually propose unifying it.

**Watch for.** The descriptor crosses the placement/content boundary
`graph_build`'s docstring draws in exactly one place: `_parse_default_mcp` in
`tools/parsers/claude_plugin_root.py` hardcodes the bundled MCP filename, and
Cursor's bundles default to `mcp.json` where Claude Code's default to
`.mcp.json`. That crossing is deliberate and should stay the only one; a second
would mean the boundary has moved and this ADR needs revisiting.

Watch also for the descriptor accreting behavior. It is data — names and
shadowing pairs. The moment a field's type becomes `Callable`, the design has
drifted toward the strategy object this decision rejected for endpoint mode.
`PluginFormat.detect` is the one existing exception, and it exists because Agent
Plugins manifests are identified by `$schema` content rather than path shape.

## When to revisit

- **If a third kind's endpoint seeding turns out to share structure with an
  existing one.** Two forks are a fork; three with a common shape are an
  abstraction waiting to be named, and the endpoint half of this decision should
  be reopened rather than worked around.
- **If a kind needs repo-mode behavior that is not expressible as names and
  shadowing pairs** — a genuinely different traversal, not a different
  vocabulary. That is the signal the descriptor has reached its limit, and the
  right response is a second `compose` path for that kind, not a callback field.
- **If `graph_build` ever needs to import `agent_kinds`.** The one-way dependency
  is load-bearing for this design; a proposal to break it invalidates the module
  layout above and needs its own ADR.
