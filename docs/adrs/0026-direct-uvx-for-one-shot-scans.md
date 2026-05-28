---
id: 0026
title: Keep only persistent install script
status: accepted
date: 2026-05-28
supersedes: 0025
superseded-by: null
---

## Context

ADR-0025 introduced two shell entry points:

- `openaca.dev/install.sh` for persistent installs.
- `openaca.dev/scan` / `scripts/scan.sh` for ephemeral one-shot scans.

The split is technically workable, but it creates more surface area
than OpenACA needs in V0: two scripts, two hosted URLs, two README
paths, and more shell/PATH behavior to review. It also forces users
to choose between installing and not installing at the exact moment
the quickstart should be boring.

The one-shot script is mostly a wrapper around a native uv command:

```bash
uvx openaca scan endpoint
```

That command remains available to users who already know uv, but it
does not need to be a primary OpenACA-documented route in V0.

## Decision

Keep `scripts/install.sh` as the only maintained shell installer in
V0. It bootstraps `uv` if needed and installs OpenACA persistently via
`uv tool install`.

Remove `scripts/scan.sh`. The README leads with a single flow:

```bash
curl -fsSL https://raw.githubusercontent.com/open-agent-security/openaca/main/scripts/install.sh | sh
openaca scan endpoint
```

We do not host or document `openaca.dev/scan` in V0. Direct `uvx`
remains native uv behavior, but it is not a primary documented
quickstart path.

Single-binary distribution paths (pyapp, Nuitka, Rust/Go rewrite)
remain deferred until distribution friction is measurably the
remaining bottleneck.

## Alternatives considered

- **Keep `scan.sh`.** Rejected. It duplicates `uvx`, adds a second
  hosted entry point, increases shell review surface, and makes the
  quickstart decision less clear without materially improving the
  one-shot path.

- **Use one shell script for both install and scan.** Rejected for the
  same reason as ADR-0025: try-once and persistent-install workflows
  have different defaults, and combining them makes the script less
  inspectable.

- **Remove all shell scripts.** Rejected. The persistent install path
  still benefits from a short, inspectable installer that bootstraps
  `uv`, installs OpenACA, and prints the exact follow-up command.

## Consequences

- (+) One fewer shell script and hosted route to maintain.
- (+) Quickstart has one clear path: install OpenACA, then run
  `openaca`.
- (+) Fewer PATH and shell-compatibility edge cases in OpenACA-owned
  code.
- (-) Users who prefer one-shot execution need to know uv/uvx already
  or read uv's own documentation. This is acceptable for V0; OpenACA
  should not introduce an extra choice before the first scan.

## When to revisit

- Users repeatedly report that installing `uv` before `uvx` is a
  material blocker for one-shot scans.
- A launch, incident-response workflow, or public audit campaign needs
  a hosted single-command scan route and the added script surface is
  worth the maintenance cost.
