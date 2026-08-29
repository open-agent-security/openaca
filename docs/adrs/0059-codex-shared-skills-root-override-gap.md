---
id: 0059
title: Skip `$HOME/.agents/skills` when `--config-dir` overrides Codex's root
status: accepted
date: 2026-08-29
supersedes: null
superseded-by: null
---

## Context

ADR-0056 granted Codex `--config-dir`/`root_override_refusal = None` on the
premise that "Codex has one root and one variable that moves it... every
in-scope surface — `config.toml`, `skills/`, `agents/`, `plugins/`, `rules/` —
lives beneath it," and that Codex "reads no other runtime's tree, so there is
no second home to strand."

ADR-0058, accepted the next day in the same PR, added exactly the surface that
premise said did not exist: `_seed_codex_shared_agent_skills` reads
`$HOME/.agents/skills` unconditionally for every installed Codex scan. That
root is not "another runtime's tree" in ADR-0056's sense (it is a shared
convention directory, not Claude Code's or Cursor's own), but it is still
home-derived content outside `config_root`, and `--config-dir` does not move
it — only `$CODEX_HOME` (or its absence) decides where `config_root` itself
resolves; `Path.home()` is read a second time, independently, inside
`_seed_codex_shared_agent_skills`.

ADR-0054's rule is a bright line, restated verbatim in ADR-0056's own "Watch
for" section as the trigger for exactly this situation: **"a kind that reads
anything home-derived after a root is named does not qualify, however small
that surface is"** and **"If Codex ever relocates a surface independently of
`$CODEX_HOME`... this decision's premise fails and it needs reopening, not
patching."** A Codex review caught this directly: `--kind codex --config-dir
<tree>` on a machine with a real `~/.agents/skills` produces a BOM stitched
from the requested root and the invoking user's actual home, indistinguishable
in the output from a correct scan — the identical failure mode ADR-0054
refused `--config-dir` over for Cursor.

## Decision

**Reopen, as ADR-0056 asked to, with a scoped answer rather than a full
reversal.** `root_override_refusal` for Codex stays `None` — every surface
`$CODEX_HOME` genuinely relocates (`config.toml`, `agents/`, `plugins/`,
`rules/`, `skills/` under the config root) remains override-honoring, unlike
Cursor's three-way split. `$HOME/.agents/skills` is the sole exception: when
`config_root` came from an explicit `--config-dir` flag,
`build_codex_installed_graph` no longer calls
`_seed_codex_shared_agent_skills`. Instead it records a coverage gap
(`graph.record_gap`), so an overridden scan reports `composition_coverage:
partial` and says why, rather than silently omitting or silently mixing
homes. A scan using `$CODEX_HOME` or the default still composes it —
`Path.home()` there is genuinely the invoking user's own home, not a foreign
one, so there is nothing to stitch.

This is possible because only *one* surface disqualifies, not three: unlike
Cursor, Codex does not also need `permissions.json`-style flag-independent
relocation or another runtime's skill roots. Skipping the one non-relocatable
surface, rather than refusing the whole flag, keeps the override useful for
the surfaces it actually specifies while removing the silent-mixing defect.

## Alternatives considered

- **Refuse `--config-dir` for Codex entirely, reversing ADR-0056.** Rejected:
  it would discard override utility for every surface that genuinely is
  `$CODEX_HOME`-relocatable (the overwhelming majority) to guard against one
  surface that is not, and it would re-require every Codex endpoint test to
  monkeypatch `Path.home()` — exactly the cost ADR-0056 named as what
  accepting the flag avoids.
- **Make `$HOME/.agents/skills` relocatable together with the override** (ADR-
  0054's deferred "treat this directory as home" design). Rejected for this
  round: it is the correct way to serve foreign-tree scanning but needs its
  own design — including whether it outranks the real `$HOME` for a directory
  that is a *shared* convention, not Codex's own — and ADR-0054 already
  deferred it once rather than bolting it onto a flag that half-fits.
- **Compose it unconditionally and rely on the `extra_roots` label alone.**
  Rejected: a stable `bom-ref` label (the earlier review round's fix) makes
  the *key* machine-independent, but the *value* — which skills actually load
  — still comes from whatever machine ran the scan, which is the defect, not
  a labeling problem.
- **Silently drop it with no gap.** Rejected: `--config-dir` exists so a scan
  can be pointed at a foreign tree (a fixture, a mounted image, a CI cache);
  silently under-reporting a real skill surface on that tree is the same
  "wrong inventory that doesn't say so" ADR-0054 already rejected once.

## Consequences

**Enables.** `--config-dir` keeps composing every genuinely relocatable Codex
surface; only the one home-derived exception is withheld, and it is withheld
honestly (a named, counted gap) instead of silently.

**Costs.** An overridden Codex scan can never report `complete` coverage for
`installed`, even when every other surface parses cleanly — the gap fires
unconditionally on override, not only when `~/.agents/skills` happens to
exist, since a scan cannot know what it chose not to look at.

**Watch for.** A second Codex surface found to read the invoking process's
home independently of `config_root` would mean this is a pattern, not an
exception, and the "refuse for symmetry with Cursor" alternative ADR-0056
rejected should be re-evaluated in that light.

## When to revisit

- If a coherent "treat this directory as home" override (ADR-0054's deferred
  alternative) is designed — it would supersede this ADR's narrower skip.
- If a second Codex surface is found reading `Path.home()` (or equivalent)
  independently of `config_root`.
