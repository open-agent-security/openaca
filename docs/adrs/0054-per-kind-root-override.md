---
id: 0054
title: A config-root override is a per-kind capability; Cursor declares none
status: accepted
date: 2026-08-26
supersedes: null
superseded-by: null
---

## Context

`--config-dir` names the config root an endpoint scan reads. With one kind
registered that was unambiguous, and [ADR-0006](0006-openaca-scan-subcommands-and-attribution.md)
records it plainly: endpoint mode reads a config directory from `--config-dir`,
defaulting to `$CLAUDE_CONFIG_DIR` then `~/.claude`. Registering Cursor
([ADR-0052](0052-cursor-agent-kind.md)) made the flag ambiguous about *which*
kind's root it named, which `--config-dir` requires `--kind` resolves.

It also exposed a second problem that flag pairing does not touch. An installed
Cursor's composition is gathered from three places, not one:

1. Cursor's own root — `mcp.json`, `skills/`, `commands/`, `agents/`, `plugins/`
2. `permissions.json`, relocated by `CURSOR_CONFIG_DIR` then `XDG_CONFIG_HOME`
3. Another runtime's skill roots — `~/.claude/skills`, `~/.codex/skills`,
   `~/.agents/skills` — which Cursor loads as a cross-tool convention

A root override moves only the first. Group 2 answers to a relocation rule the
flag is deliberately not part of — honouring `--config-dir` there would look in
the wrong place for anyone who relocated the file the way Cursor actually
supports. Group 3 was never under Cursor's root, so there is nothing to move.
`--kind cursor --config-dir <tree>` therefore produces a composition stitched
from two homes, presented as one agent, with nothing in the output separating it
from a correct scan.

Claude Code has no equivalent exposure, and the reason is structural rather than
tidiness. Its home directory is consulted in exactly one place — the function
computing the default root. Name a root and home is never read again, and Claude
Code does not read another runtime's directories. **For Claude Code home is a
default; for Cursor home is an ingredient.**

No caller relies on the current behaviour. The tests that need a hermetic Cursor
root fake the home directory rather than passing `--config-dir`, precisely
because the flag cannot isolate a Cursor scan.

This does not contradict ADR-0006, whose unconditional phrasing describes the
only kind that existed when it was written, so it is not superseded here.
ADR-0052 needs no amendment either: it records what Cursor's surfaces are, not
how an invocation addresses them.

## Decision

A config-root override is a **per-kind capability**, granted only to a kind for
which naming a root fully specifies the target — meaning no part of that kind's
composition is derived from the home directory once a root is named. Claude Code
qualifies and is unaffected. Cursor declares no relocatable root: `--config-dir`
alongside `--kind cursor` is rejected with an error naming the reason, and an
installed Cursor scan always resolves `<home>/.cursor` and the invoking user's
home. `--kind` remains optional, becoming mandatory only alongside
`--config-dir`. Scanning a Cursor tree that is not the invoking user's home is an
explicitly unserved need.

## Alternatives considered

- **Keep the flag for Cursor and document the trap.** Rejected. The failure is
  silent: a mixed-home composition is indistinguishable in the output from a
  correct one, so the only thing standing between a user and a wrong inventory is
  a warning in a spec they may never read. A footgun a document apologises for is
  still a footgun, and inventory data that is wrong without saying so is worse
  than a missing capability.
- **A "treat this directory as home" override that moves every home-derived
  group together.** Not rejected on merit — this is the correct way to serve
  foreign-tree scanning, and it would let the tests stop faking `Path.home`. It
  is deferred because it needs its own design, including whether it outranks
  `CURSOR_CONFIG_DIR` and `XDG_CONFIG_HOME` for `permissions.json` (a scan of
  someone else's tree has no business honouring the invoking process's
  environment, but saying so makes an env-relocated file unreachable). Recorded
  as the shape a future solution takes rather than bolted onto a flag that
  half-fits.
- **Infer the kind when only `--config-dir` is given**, since Claude Code is now
  the only kind that accepts one. Rejected. The inference is correct exactly
  until a third relocatable kind registers, at which point the same command
  either starts erroring or silently changes which kind it meant. Explicit
  selection costs one flag and never changes meaning.
- **Make Cursor's root relocatable by widening `--config-dir` to also move groups
  2 and 3.** Rejected. Group 2 would break the relocation rule Cursor's runtime
  actually implements, and group 3 would require reconstructing a home directory
  from a `.cursor` path's parent — the basename-reconstruction this kind's spec
  forbids.

## Consequences

Foreign-tree Cursor scanning is unsupported: a copied config, a mounted image, a
CI cache, or another account on a shared machine cannot be scanned as a Cursor
endpoint. Tests needing a hermetic Cursor root keep faking the home directory,
which is what they already do. Claude Code invocations are unchanged.

Because the capability is a declaration on the kind rather than a property of the
flag, a third kind states its own answer instead of inheriting one, and the CLI
error can name the specific reason a given kind refuses. The cost is one more
field a kind author must reason about, and a rule that is easy to get wrong in
the permissive direction — a kind that reads *anything* home-derived after a root
is named does not qualify, however small that surface is.

Implementation follows this decision rather than preceding it: until it lands,
the CLI still accepts `--config-dir` for Cursor, and the spec's stated contract
is ahead of the code. That divergence is the tracked follow-up, not an
undiscovered inconsistency.

## When to revisit

- A coherent home override is designed — the second alternative above becomes the
  decision, and this ADR is superseded rather than amended.
- Cursor ships a whole-root relocation variable, which would make its root
  relocatable in the runtime's own terms and requalify it.
- A third kind needs an override whose semantics differ from both a full root
  replacement and an outright refusal.
- Cursor stops reading another runtime's skill roots, or `permissions.json` stops
  relocating independently: either would collapse the three groups toward one and
  remove the reason for the refusal.
