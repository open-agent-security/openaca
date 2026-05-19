---
id: 0017
title: Endpoint scan keeps project context opt-in, with an unconditional reminder note
status: accepted
date: 2026-05-19
supersedes: null
superseded-by: null
---

## Context

Round 1 of closed-beta testing surfaced an ergonomics failure in
endpoint mode. A tester running `openaca scan endpoint -v` from a
Claude Code project directory (with 10+ skills under
`<repo>/.claude/skills/`) saw:

```
0 active plugins, 0 direct components, 0 total components
```

The scanner behaved per ADR-0006 — project settings opt-in via
`--project <repo>`, so no `--project` meant no project context. The
output was technically correct but the tester had to read `--help`
to discover the flag. He called it a "nitpick" but the friction was
real, and other testers will hit the same wall.

The natural fix considered first was "default `--project` to cwd."
That direction is rejected after considering the home-directory
case: cwd-as-default at `~` causes `_walk_project_skill_dirs` to
recursively walk the entire home tree (seconds-to-tens-of-seconds
of stat calls, plus double-counting user-scope config as
project-scope). Workarounds (overlap guards, "only-when-cwd-has-
markers" heuristics, cwd-based hint triggers) all reintroduce
invisible behavior the user has to understand.

The chosen design is the simplest one that solves the round-1 case
without introducing any hidden logic.

## Decision

`openaca scan endpoint` keeps `--project` opt-in (ADR-0006 stands on
this point). When `--project` is omitted, the scan emits an
**unconditional reminder note** at the end of output:

```
detected config_dir=<config_dir>, project=(none) (mode=endpoint)
Scanned N active plugins, M components ...

Note: scanned user-level config only. To include project-local
skills, MCPs, and plugin manifests, pass --project /path/to/project
(or --project . for the current directory).
```

When `--project` is provided, the note is suppressed (the user has
made the choice; the educational message is no longer needed).

The `detected config_dir=..., project=...` line is emitted
**unconditionally** (not just in `-v` mode) — transparency, not
surprise. `project=(none)` appears when no `--project` was passed.

There is no `--no-project` flag, no cwd-as-default behavior, no
overlap guard, and no cwd-marker detection. The whole design fits
in one sentence: "endpoint scan is user-level only; pass `--project`
to add project context; the CLI tells you about the flag every time
you don't pass it."

## Alternatives considered

- **Default `--project` to cwd**: the natural read of the round-1
  case. Rejected because it triggers a recursive walk of the home
  tree when cwd is `~`, double-counts user-scope as project-scope,
  and silently changes scan scope based on where the user happens
  to be cd'd. PR review on #69 escalated this from "noisy counts"
  (which ADR draft 1 accepted) to "expensive walk + incorrect
  output" (which is unacceptable).

- **Default to cwd + overlap guard**: skip cwd-as-default when cwd
  contains the config dir. Rejected: the guard is itself a
  heuristic — behavior differs invisibly between adjacent cwds
  (e.g., `~/workspace` includes project context; `~` doesn't), and
  the user has no way to predict which case applies without reading
  the `detected` line and inferring the rule.

- **Hint-first with cwd-has-markers detection**: no default, but
  show the reminder note only when cwd has `.claude/` or `.mcp.json`.
  Rejected: this hides the educational mechanism behind a detection
  rule the user has to understand. If a tester runs from a nested
  subdirectory without top-level markers, the hint won't fire —
  surprise. The unconditional note avoids this entirely.

- **Default to opt-in with no reminder (ADR-0006 status quo)**:
  rejected by round-1 evidence. "Had to rely on command help
  options" is the failure mode the note is designed to fix.

- **Walk up to find the nearest `.claude/` ancestor**: more useful
  than cwd literal when invoked from a nested subdir. Rejected for
  V0: same invisible-rule problem as overlap guard. If round-2
  evidence shows nested invocation is common, can be reconsidered.

## Consequences

- **Pro**: simplest design that solves the round-1 case. One rule,
  no edge cases.
- **Pro**: home-dir footgun does not exist. No recursive walk
  unless the user explicitly asks for project context.
- **Pro**: no `--no-project` needed; CLI surface stays minimal.
- **Pro**: the educational note is *visible* and *consistent*. A
  tester who runs endpoint scan three times in a row sees the note
  three times — annoying enough to learn the flag, not annoying
  enough to be a blocker.
- **Con**: testers running from a project dir pay a one-iteration
  cost: first scan shows 0 components + the note, second scan with
  `--project .` shows the real components. One framing of this is
  "bad first scan"; the note converts it into a "good-enough first
  scan with clear next step."
- **Con**: the note appears every time `--project` is omitted, even
  for users who already know about the flag and chose to omit it
  intentionally. Mitigation: it's a single short paragraph on
  stderr, easy to grep out. If round-2 testers complain, add a
  `--quiet` or `OPENACA_QUIET=1` opt-out.
- **Con**: amends ADR-0006's "project settings are opt-in" framing
  by adding the reminder note. The opt-in itself is preserved; the
  rest of ADR-0006 (subcommand structure, `claude-plugin` ecosystem,
  `attributed_to` fields) still applies unchanged.

## When to revisit

- If round-2 testers consistently miss the note (read past it, dismiss
  it, etc.), reconsider whether a stronger UX signal is needed
  (e.g., red text, bold, exit-code policy).
- If testers running from project dirs find the per-invocation note
  noisy enough to file as feedback, add a quiet mode.
- If endpoint mode grows host-provided "current project" hints
  (e.g., IDE integration), reconsider whether the opt-in default
  can be replaced by a hint-from-host model.
- If the design space accumulates enough invisible-behavior
  pressure that the unconditional-note discipline starts feeling
  arbitrary, that's a signal to revisit the underlying default
  choice.
