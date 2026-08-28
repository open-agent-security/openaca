---
id: 0056
title: Codex honors $CODEX_HOME and accepts --config-dir
status: accepted
date: 2026-08-27
supersedes: null
superseded-by: null
---

## Context

ADR-0054 established that a kind declares whether a root override is honest for
it, and refused `--config-dir` for Cursor. The refusal was reasoned from
Cursor's specific shape: an installed Cursor's composition is gathered from
three separately-relocated places — its own root, `permissions.json` (relocated
independently by `CURSOR_CONFIG_DIR`/`XDG_CONFIG_HOME`), and another runtime's
skill roots under the user's home — so an override moved only the first and
produced a composition stitched from two homes that the output could not
distinguish from a correct scan.

That reasoning has been read since as though the refusal were the general rule
and acceptance the exception. Codex is the case that shows it is not.

## Decision

**Codex honors `$CODEX_HOME` and accepts `--config-dir`.**
`root_override_refusal` is `None`.

Codex has one root and one variable that moves it. `$CODEX_HOME` (60 references
in the audited binary) relocates the whole config root; every in-scope surface —
`config.toml`, `skills/`, `agents/`, `plugins/`, `rules/` — lives beneath it.
Codex reads no other runtime's tree, so there is no second home to strand:
`.claude/skills`, `.claude/agents`, and `installed_plugins.json` all have zero
references.

The condition ADR-0054 refused on is therefore absent, and a scan rooted at an
overridden directory is complete rather than stitched.

`--config-dir` continues to require `--kind` once more than one kind is
registered — that guard is about ambiguity between kinds and is unrelated to
whether any particular kind can honor the flag.

**ADR-0054 is not superseded.** Its decision for Cursor stands unchanged, and
its per-kind mechanism is exactly what is being exercised here: a kind declares
whether an override is honest for it, and Codex's answer is yes. Recording that
answer as its own ADR makes the mechanism's two-sidedness visible, so the next
kind reasons from its own shape rather than copying whichever precedent it read
first.

## Alternatives considered

- **Refuse `--config-dir` for symmetry with Cursor** — rejected. The refusal
  would be unreasoned: ADR-0054's premise is a multi-home composition, and Codex
  has one home. Symmetry between kinds is not a value the mechanism holds; each
  kind declaring its own truth is.
- **Honor `$CODEX_HOME` but still refuse the flag** — rejected as incoherent. If
  the root genuinely relocates, and an environment variable may relocate it,
  there is no principled reason to reject the explicit form of the same request.
  It would also leave endpoint tests unable to root a fixture without mutating
  the environment.
- **Amend ADR-0054 in place to say the refusal was Cursor-specific** — rejected
  on supersession discipline: accepted ADRs are immutable, and old PRs need to be
  readable against the rules in force when they were written.
- **Extend the refusal string mechanism to carry a positive reason too** —
  rejected as unnecessary machinery. `None` already means "accepted"; a kind that
  accepts needs no prose in the CLI.

## Consequences

**Enables.** Codex endpoint tests can root a fixture directory directly, without
monkeypatching `Path.home` — the workaround every Cursor endpoint test needs.
CI runners that set `$CODEX_HOME` are scannable in place.

**Costs.** Two registered kinds now answer `--config-dir` differently, and the
error message a user sees depends on which kind they named. That is the
mechanism working as designed, but it is surprising the first time.

**Watch for.** If Codex ever relocates a surface independently of `$CODEX_HOME`
— the way Cursor relocates `permissions.json` — this decision's premise fails
and it needs reopening, not patching. `managed_config.toml` is the candidate to
watch: its distribution path has not been audited, and if it sits outside the
root then an override again produces a partial composition.

## When to revisit

- **If any in-scope Codex surface stops resolving beneath `$CODEX_HOME`.**
- **When `managed_config.toml` is audited**, since an admin layer outside the
  root would reintroduce exactly the split-home problem ADR-0054 refused on.
