---
id: 0059
title: The shared `.agents` skills root relocates with `--config-dir`
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

**Reopen, as ADR-0056 asked to, and close it by making the companion root move
with the flag.** `root_override_refusal` for Codex stays `None`, and a scan
using `--config-dir` composes a **complete** endpoint rather than one missing a
surface.

Codex reads `$HOME/.agents/skills`, which `$CODEX_HOME` does not relocate — so
by default that root is the invoking user's own home, and there is nothing to
stitch. `--config-dir` is different: it names a root explicitly, and ADR-0054
grants that flag only to a kind for which naming a root **fully specifies the
target**. So naming a root moves its companion too. On a real endpoint `.codex`
and `.agents` are siblings under the home directory, which makes
`<dir>/../.agents` the faithful relocation, and
`codex.resolve_shared_skills_root` the one place that decides it.

ADR-0056's premise — "Codex reads no other runtime's tree, so there is no
second home to strand" — was made false by ADR-0058 adding the shared skills
read. This restores it in the only way that keeps both ADRs true at once: there
is still exactly one root to name, because naming it moves everything that
hangs off it.

## Alternatives considered

- **Skip the shared root under an override and record a coverage gap** — the
  first form of this decision, reversed. It keeps the flag and reports honestly
  that something was omitted, but it ships a flag that knowingly returns an
  incomplete composition, which is precisely the outcome ADR-0054 judged worse
  than having no flag at all. "Correct but partial, and it says so" is a weaker
  contract than "names one directory, gets everything".
- **Refuse `--config-dir` for Codex, as ADR-0054 does for Cursor** — rejected.
  Cursor disqualifies because *three* groups relocate on different axes and one
  of them is another runtime's tree. Codex has one config root plus one
  companion directory that moves with it, so the flag can still fully specify
  the target; refusing would remove the endpoint-test isolation the flag
  provides for no gain in correctness.
- **Read the real `$HOME/.agents/skills` even under an override** — rejected.
  That stitches the requested root together with whatever the scanning
  machine's home happens to hold, which is the split-home failure ADR-0054
  named. It is also the one variant that can *upload* unrelated inventory.

## Consequences

**Enables.** `--config-dir` composes every genuinely relocatable Codex surface,
including the `.agents/skills` companion — `resolve_shared_skills_root` relocates
it to `<dir>/../.agents` instead of withholding it, so an overridden scan can
report a **complete** `installed` composition rather than one that knowingly
skips a surface.

**Costs.** The relocation is a convention, not a guarantee: it assumes `.codex`
and its `.agents` companion stay siblings under the named root's parent. A
fixture or endpoint built without that sibling relationship composes an empty
shared-skills root under `--config-dir`, the same as a real endpoint whose user
simply has no shared skills — the two are indistinguishable, which is the
accepted trade-off for treating "name a root" as fully specifying the target.

**Watch for.** A second Codex surface found to read the invoking process's
home independently of `config_root` would mean this is a pattern, not an
exception, and the "refuse for symmetry with Cursor" alternative ADR-0056
rejected should be re-evaluated in that light.

## When to revisit

- If a coherent "treat this directory as home" override (ADR-0054's deferred
  alternative) is designed — it would supersede this ADR's narrower skip.
- If a second Codex surface is found reading `Path.home()` (or equivalent)
  independently of `config_root`.
