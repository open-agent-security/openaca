---
id: 0017
title: Endpoint scan defaults project context to cwd
status: accepted
date: 2026-05-19
supersedes: null
superseded-by: null
---

## Context

Round 1 of closed-beta testing surfaced a high-severity ergonomics
failure in endpoint mode. A tester running `openaca scan endpoint -v`
from a Claude Code project directory (with 10+ skills under
`<repo>/.claude/skills/`) saw:

```
0 active plugins, 0 direct components, 0 total components
```

The scanner behaved per ADR-0006 — project settings were opt-in via
`--project <repo>`, and no `--project` flag meant no project context.
The output was technically correct but produced the worst possible
first-scan experience: a real environment with real components,
reported as empty, with no hint that project context was missing.

ADR-0006 documented the opt-in default. That default was inherited
from the original "endpoint scan = installed Claude Code endpoint
only" framing, before round-1 testers tried it from project dirs
the way they use Claude Code itself.

## Decision

`openaca scan endpoint` defaults the project context to the current
working directory. The user's mental model from Claude Code is that
running from a project dir includes that project's components — the
scanner matches that.

CLI surface:

- `openaca scan endpoint` — user-level config + `cwd` as project
  context (default).
- `openaca scan endpoint --project /path/to/other` — override the
  default; named project replaces cwd.
- `openaca scan endpoint --no-project` — skip project context
  entirely; scan only user-level endpoint config.
- `--project` and `--no-project` are mutually exclusive; passing both
  raises `click.UsageError` rather than silently picking one.

The scan emits the resolved scope unconditionally (not just in
verbose mode):

```
detected config_dir=<config_dir>, project=<resolved>|(none) (mode=endpoint)
```

This makes the scope transparent — the user always sees exactly what
was scanned, so cwd-as-default never produces hidden behavior.

## Alternatives considered

- **Hint-first (Codex's initial proposal)**: keep the opt-in default,
  but when 0 components were found AND cwd contains Claude artifacts
  (`.claude/`, `.mcp.json`), emit a hint:
  *"No project context was included. Try `openaca scan endpoint
  --project .`"*
  Rejected: imposes friction on every tester for one round of
  evidence we already had from round 1. The hint just delays the
  same surprise to the next user who doesn't read it carefully. Also
  introduces a heuristic (what counts as "Claude artifacts"?) that
  adds complexity without solving the underlying mental-model
  mismatch.

- **Heuristic-based default**: default to cwd only when cwd contains
  `.claude/` or `.mcp.json`; otherwise no project context.
  Rejected: heuristic complexity (what triggers? what if my project
  uses a different layout?) doesn't earn its keep. Always-default-to-
  cwd needs one sentence of documentation; the heuristic needs a
  flowchart.

- **No default; keep opt-in via `--project`**: the ADR-0006 status
  quo.
  Rejected by round-1 evidence: produces the worst possible
  first-scan experience for the dominant use case (running from a
  project dir).

- **Walk up to find the nearest `.claude/` ancestor**: more useful
  than `cwd` literal when the user is in a nested subdirectory.
  Rejected for V0: reintroduces a heuristic. If round-2 evidence
  shows nested-dir invocation is common, can be added.

## Consequences

- **Pro**: matches the Claude Code mental model; first-scan
  experience produces signal instead of zeros.
- **Pro**: removes a flag from the dominant invocation. `openaca
  scan endpoint` is the natural form.
- **Pro**: explicit output line makes the scope transparent — no
  silent behavior change risk.
- **Con**: if a user runs from `~` or another non-project dir, the
  scanner walks that dir looking for project-local artifacts. Cost
  is the walk; output reports `0 components` from that path. Not
  catastrophic; documented via the `--no-project` escape hatch.
- **Con**: if a user runs from `~` specifically, project-root
  `~/.claude/...` overlaps with `install_root`'s `~/.claude/...`.
  V0 does not dedupe — the user sees inflated counts. Mitigation:
  `--no-project` is documented as the way to scan only user-level
  config regardless of cwd. If testers hit this in round 2, add
  install-root/project-root overlap detection.
- **Con**: amends ADR-0006's "project settings are opt-in" framing.
  The rest of ADR-0006 (subcommand structure, `claude-plugin`
  ecosystem, `attributed_to` fields) still applies; only the
  project-default is changed by this ADR.

## When to revisit

- If round-2 testers running from nested project subdirectories
  report missing project context (the walk-up-to-find-`.claude`
  alternative becomes worth implementing).
- If the `~`-as-cwd overlap case turns out to be common in real
  usage, add a dedup or guard against it.
- If endpoint mode grows additional default-context sources (e.g.,
  the active editor's workspace, a host-provided "current project"
  hint), the `cwd` default may need to be relativized against those.
