---
id: 0057
title: Endpoint seeding is descriptor-driven where kinds share a procedure; acquisition forks
status: accepted
date: 2026-08-27
supersedes: null
superseded-by: null
---

## Context

OpenACA builds a composition graph two ways.

**Repo mode** walks a directory tree and matches config files by path. **Endpoint
mode** inspects a runtime actually installed on a machine, where globbing does
not work: "which plugins does this agent have?" is not answered by files lying
around, it is answered by reading the runtime's own install bookkeeping.
*Seeding* is that reading. For Claude Code, `_seed_endpoint` intersects two
records —

```
installed_plugins.json  ∩  settings.json enabledPlugins  →  the active plugins
```

— then descends into each plugin's install path for bundled skills, and reads
`mcpServers` from settings for remote MCP.

When Cursor registered, ADR-0053 had to decide whether it reused that machinery.
It split the answer:

| Mode | Decision | Why |
|---|---|---|
| Repo | **parameterized** — one shared walker driven by a data descriptor | only the string literals differed (`.claude` vs `.cursor`); the ~600 lines around them were identical |
| Endpoint | **forked** — each kind brings its own seed | the *control flow* differed: Cursor has no install lockfile, no settings layers, and no readable enable state, so there is nothing to intersect |

ADR-0053 named the condition for revisiting the endpoint half: a third kind whose
seeding shares structure with an existing one. Codex looked like that kind. Its
plugin enable map is keyed byte-identically to Claude Code's
(`superpowers@claude-plugins-official`), its cache layout is
`plugins/cache/<mkt>/<name>/<ver>/`, and it records a commit pin.

**That comparison was of the data, and it does not settle the procedure.** A
first draft of this decision concluded from it that endpoint seeding could be
parameterized whole. Checking branch by branch — which this ADR's own scope
statement demanded rather than assumed — shows it splits:

| Branch | Claude Code | Codex | Same procedure? |
|---|---|---|---|
| Project skills | `.claude/skills/` | `.codex/skills/` | **yes** — a name differs |
| Direct components | `<root>/skills`, `commands`, `agents` | `<root>/skills`, `agents` | **yes** — names and an absence |
| Plugin acquisition | open lockfile, open settings, **intersect** | read a TOML table, **enumerate a cache directory** | **no** |
| Remote MCP | read `mcpServers` from settings | read `[mcp_servers.*]` from `config.toml` | **no** |

The bottom two differ in the number of files opened, the order, and the operation
combining them. "Intersect two records" and "read a table, then enumerate a
directory" are not one procedure with different labels. Both kinds *record* the
same facts; they *acquire* them differently — which is precisely the limit the
first draft of this ADR wrote down for itself: *"a genuinely different acquisition
model, not a different vocabulary."*

## Decision

**Endpoint seeding is descriptor-driven for the branches that share a procedure,
and forked for the branches that do not.**

- **Parameterized:** project skills and direct components. `_seed_endpoint` and
  `_seed_direct_components` take an `EndpointSurface` descriptor carrying the
  directory names each kind reads. Claude Code's values are transcribed verbatim.
- **Forked:** plugin acquisition and remote MCP. Codex brings its own narrow seed
  for these two, invoked from `tools/agent_kinds/codex.py`'s `_compose`, exactly
  as Cursor's is.

**Only the parameterized half of ADR-0053's endpoint decision is replaced.** Its
repo-mode half — `RepoSurface`/`PluginFormat`/`BundledLayout`, the one-way
`agent_kinds → graph_build` dependency, the public-alias rule, the uncontaminated
regression gate — stays in force, and its endpoint fork remains correct for the
acquisition branches.

The frontmatter says `supersedes: null` and ADR-0053 keeps `status: accepted`.
The repository's supersession discipline assumes a decision is replaced whole;
marking ADR-0053 superseded would retire decisions that remain in force. **This
deviation is deliberate and is the thing in this ADR most worth a reviewer's
attention.**

**`EndpointSurface` carries data only.** No field may be typed `Callable`, and no
field may be a mode discriminator that selects between differing control flows
inside the shared function. A `Literal["intersect", "enumerate"]` switch would be
branching wearing a data costume, and it is rejected below for that reason. Where
a kind has no counterpart for a parameterized branch, the descriptor carries an
absence — `None`, `()` — never a switch.

**Claude Code's behavior must not change.** The parameterization lands with Claude
Code's values transcribed verbatim, under a byte-identical golden-graph gate: a
fixture endpoint's Claude Code graph is identical before and after, with zero
Codex entries in that diff.

## Alternatives considered

- **Parameterize all four branches** — the first draft of this decision, reversed.
  Rejected on the branch-by-branch check above: two of the four differ in
  acquisition procedure, not vocabulary. The error was reading a data-model match
  (both kinds record marketplaces, a cache, an enable map, a commit pin) as a
  procedure match. Same destination, different route.
- **A `Literal[...]` discriminator (`plugin_acquisition`, `mcp_source`) dispatched
  inside `_seed_endpoint`** — rejected. It keeps one function at the cost of
  putting an `if` over two genuinely different procedures in the middle of Claude
  Code's most exercised path, and it makes the descriptor a control-flow selector
  rather than data. That is the drift both ADR-0053 and this ADR name as the
  failure mode; a field's *type* being non-`Callable` is not the test, its
  *function* is.
- **Fork all four branches for Codex** — rejected. Project skills and direct
  components genuinely are literal substitutions, and forking them re-earns the
  containment and normalizer behavior the shared path already encodes.
- **Let Codex import Claude Code's seed as a private** — rejected. A cross-module
  private import is an interface with no declaration, which ADR-0053 already
  rejected for the repo-mode helpers.
- **Mark ADR-0053 fully superseded** — rejected because its repo-mode half is live
  and its endpoint fork remains correct for the acquisition branches.

## Consequences

**Enables.** The two branches that are genuinely shared stop being duplicated per
kind. A future kind adds directory names for those, and writes acquisition code
only where its acquisition genuinely differs.

**Costs.** `_seed_endpoint` is Claude Code's most heavily exercised path, and this
touches it for a smaller payoff than the first draft promised — two branches, not
four. The golden-graph gate is not optional; without it, any change in Claude
Code's endpoint output becomes an argument rather than a defect.

The endpoint path is now split three ways in the reader's mind: shared and
parameterized (skills, direct components), forked per kind (acquisition), and
Claude-Code-only (the tier-2 lockfile dependency walk, which has no counterpart in
either other kind). That is more structure than "forked" was, and someone will
propose collapsing it.

**Watch for.** A future kind whose acquisition *does* match Claude Code's
intersect-two-records shape. Two kinds sharing an acquisition procedure is the
signal to parameterize that branch too — and at that point the question is which
two, not whether.

## When to revisit

- **If a third kind's plugin acquisition matches an existing kind's procedure**,
  not merely its data model. The distinction is the whole content of this ADR:
  compare the steps, not the records.
- **If the parameterized branches stop being expressible as directory names** — a
  genuinely different traversal for project skills or direct components. That is
  the descriptor reaching its limit.
- **If the tier-2 lockfile dependency walk turns out to have no counterpart in any
  future kind**, in which case it belongs to Claude Code alone and should be named
  as such rather than sitting inside a shared function.
