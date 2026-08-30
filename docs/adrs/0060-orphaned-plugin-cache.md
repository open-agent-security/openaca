---
id: 0060
title: Inventory an orphaned plugin cache, and mark it not installed
status: accepted
date: 2026-08-30
supersedes: null
superseded-by: null
---

## Context

Codex caches a plugin bundle under `$CODEX_HOME/plugins/cache/<marketplace>/<name>/<version>/`
and records that it is installed elsewhere — `[plugins."<name>@<marketplace>"]` for the enable
state, `[marketplaces.<name>]` for the registry it came from. Those three facts can disagree,
and on the endpoint that motivated this they did.

Three bundles sat under `openai-curated-remote`. `codex plugin marketplace list` does not report
that marketplace, `config.toml` does not declare it, and no `[plugins.*]` entry names any of its
bundles. Codex cannot load them. OpenACA reported all three as `enabled = true`, and because
everything inside a plugin inherits its identity (`_plugin_private_identity`), they also
contributed ~40 unidentified components — a third of that endpoint's inventory, for plugins the
agent does not have.

The composition rule this sits under is that a cached bundle with no enable-map entry defaults to
enabled and warns. That default is right for the ambiguous case — a bundle from a registry Codex
still composes, missing only its enable record — and wrong here, where the marketplace itself is
gone. Both signals absent is a different state from one signal absent, and until Codex shipped a
third agent kind there was nothing that distinguished them.

## Decision

A cached bundle with **neither** an enable-map record **nor** any marketplace declaration —
`[marketplaces.*]` in any active config layer, or a marketplace manifest under `$CODEX_HOME/.tmp/`
— is reported `enabled = false` and `installed = false`, and stays in the inventory.

`installed` is emitted only when false, so absence means installed. It exists because
`enabled = false` alone conflates two states that are equally inert and not equally actionable: a
plugin switched off in config is installed and one edit away from running, while an orphan has no
marketplace left to enable it from. Every component inside an inactive plugin inherits `enabled`,
inherits `installed` when the parent is orphaned, and carries `inactive_via` naming the plugin
that decided it — enable state belongs to the container, and a skill has no switch of its own.

## Alternatives considered

- **Drop orphaned bundles from composition entirely**: the semantically cleanest reading — "not
  installed" means "not inventoried", and it would collapse the duplicate rows a stale cache
  produces. Rejected because it fails in the false-negative direction. The evidence that a bundle
  is orphaned is the *absence* of a declaration in the sources we read, and Codex has already been
  observed composing a marketplace from a source we did not read (ADR-0059's sibling finding: a
  self-declared manifest under `.tmp/`). If another such source exists, dropping silently deletes
  real installed plugins from a security inventory, and the failure is invisible. Marking them
  inert is wrong in the harmless direction; dropping them is wrong in the harmful one.

- **Keep reporting them `enabled = true`**: the prior behaviour, and defensible as
  over-reporting toward active. Rejected because it is not over-reporting, it is a false positive
  with no counter-evidence: the runtime's own `plugin marketplace list` does not know the
  marketplace exists. Over-reporting is the safe direction when a state is *unknown*, not when it
  is knowable and known.

- **One field, `enabled`, for both states**: fewer properties to carry and to explain. Rejected
  because the two states differ in what a reader can do about them. Offering "re-enable" for a
  bundle whose marketplace is gone sends someone looking for a switch that does not exist, and a
  console cannot tell them apart from one field.

- **Suppress the orphan's components rather than the plugin**: keeps the plugin row visible as
  evidence while hiding its payload. Rejected because it splits one fact across two
  representations — the plugin would be listed as present while the files it is made of were not
  — and because inheritance already gives every component the parent's state without inventing a
  second rule.

## Consequences

An endpoint with a stale plugin cache reports more components than it runs, each carrying the
state that says so. Consumers decide what to do with that: OpenACA Cloud hides `installed = false`
components from its inventory by default behind a toggle, and its CSV export lists them with a
state column instead, on the grounds that an export means everything.

Duplicate names survive — the same skill from a live marketplace and from a stale one are two
rows, distinguishable by identity and state rather than collapsed. That is the honest reading:
they are two directories on disk.

The `enabled = true` default for a bundle with a known marketplace and no enable record is
unchanged, and is now the *only* case that default covers. If a future audit finds Codex loading
bundles from a marketplace it does not report, this decision is what has to be revisited: the
orphan test would then be wrong rather than merely conservative, and the cost would be a component
marked inert that the agent actually runs.
