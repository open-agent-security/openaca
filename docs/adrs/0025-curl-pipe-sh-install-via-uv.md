---
id: 0025
title: OSS install via uv + curl-pipe-sh; defer single-binary distribution
status: superseded
date: 2026-05-28
supersedes: null
superseded-by: 0026
---

## Context

Distribution friction raises the prerequisite count for a
Python-based CLI: users must navigate Python version selection,
package isolation (PEP 668, venv vs. pipx vs. pip --user), and PATH
wiring before running a first scan. The benchmark for a frictionless
CLI install is a single command with no runtime selection, no
isolation decision, and no PATH wiring — languages with
self-contained binary distributions (Go, Rust) achieve this
naturally via their package installers.

OpenACA today asks the user to:

1. Verify they have Python 3.11+ (macOS 14 ships 3.9; many distros vary).
2. Pick between `uv`, `pipx`, `pip --user`, or a venv to avoid
   polluting system Python and tripping PEP 668.
3. Resolve PATH so `openaca` is callable.
4. Optionally pin a version for reproducibility.

That's roughly five mental hops between "I read about OpenACA" and
"I ran a scan." A single-command CLI install is one hop. Reducing
that prerequisite count lowers the barrier to first use.

A previous brainstorm enumerated five paths to close this gap, ranked
roughly by engineering cost: curl-pipe-sh via uv (half day) →
Homebrew tap (one day) → pyapp single-binary (~one week) →
Nuitka/PyOxidizer full-bundle (~two weeks) → Rust/Go scanner rewrite
(months). The cheapest paths capture most of the friction-reduction
benefit; the expensive paths capture the last ~20% and lock in
operational ongoing cost (cross-compile per-release, code signing,
binary distribution channel maintenance).

## Decision

V0 OSS install ships via two curl-pipe-sh entry points hosted at
`openaca.dev`, both backed by `uv` as the bridge that handles Python
version + isolation + PATH:

- **`openaca.dev/install.sh`** — persistent install. Bootstraps `uv`
  if needed, then `uv tool install --upgrade openaca`. User ends up
  with `openaca` on PATH; subsequent invocations are direct.
- **`openaca.dev/scan`** — ephemeral one-shot. Bootstraps `uv` if
  needed, then `uvx openaca scan endpoint`. OpenACA is NOT
  permanently installed; uv resolves and runs a throwaway environment
  for that single invocation. Try-before-install path: scan without
  committing to a persistent install.

Both scripts honor `OPENACA_VERSION` for pinning. The default is
`latest`; the documented pinning convention is:

| Use case | Default |
|---|---|
| Casual OSS user / blog reader | `latest` |
| Audit campaign / demo | `latest` |
| Fleet MDM rollout | pinned (e.g. `OPENACA_VERSION=0.2.0b1`) |
| CI / reproducibility | pinned |

The README leads with the curl-pipe-sh commands. `uv tool install` and
`pip install` are demoted to a "manual install" section for users who
prefer them. Single-binary distribution paths (pyapp, Nuitka, Rust/Go
rewrite) are explicitly deferred until friction is measurably the
remaining bottleneck.

## Alternatives considered

- **Rewrite the scanner in Rust or Go.** Rejected for V0. Multi-month
  engineering cost without proven distribution bottleneck; throws
  away the shipped Python codebase. Worth revisiting in a year if
  curl-pipe-sh + Homebrew don't close the friction gap.

- **pyapp single-binary distribution.** [pyapp](https://github.com/ofek/pyapp)
  wraps a Python app as a Rust binary that downloads Python on first
  run. ~5MB binary, ~30s first-run download, matches the distribution
  story of single-binary CLIs. Rejected for V0 because it adds release
  machinery (cross-compile per target, code signing on macOS) before
  friction has been measured. Revisit when curl-pipe-sh adoption
  reports indicate uv-bootstrap is itself a friction layer worth
  eliminating.

- **PyOxidizer or Nuitka full-bundle.** PyOxidizer is archived
  upstream (2024). Nuitka works but has gotchas with C-extension
  libraries and dynamic imports. pyapp is the modern maintained
  equivalent; if we ever ship single-binary, that's the right tool.

- **Homebrew tap as the primary install path.** Rejected as primary
  because it serves only macOS users (Linux still needs curl-pipe-sh
  or distro packages). Added as a complementary path in a follow-up:
  macOS users default to `brew install`, everyone else uses
  curl-pipe-sh.

- **Keep `pip install openaca` as the primary documented path.**
  Rejected. PEP 668, Python version skew, system-Python pollution,
  and PATH wiring are real obstacles even for users who
  have Python installed. The cost of writing two ~30-line shell
  scripts is dramatically lower than the cost of every README reader
  hitting one of those hops.

- **Use a single script that does both install and run.** Rejected.
  Different user journeys (try-once vs. install-permanently) want
  different defaults; bundling them couples concerns and complicates
  the script. Two short scripts read better than one decorated one.

## Consequences

- (+) Install friction reduced from ~5 mental hops to 1
  (`curl ... | sh`).
- (+) Try-before-install path via `uvx` — scan without committing to
  a permanent install; useful for blog readers, audit campaigns, and
  incident-response moments.
- (+) Half-day of engineering, no substrate change to the scanner.
  Reversible if the approach turns out wrong.
- (+) `OPENACA_VERSION` pin gives Fleet/MDM/CI the reproducibility
  they need; the same scripts serve casual and enterprise users.
- (–) Adds an operational dependency on `openaca.dev` DNS + serving
  (Cloudflare Pages / Vercel / GitHub Pages). The scripts are also
  in-repo at `scripts/install.sh` and `scripts/scan.sh` so the GitHub
  raw URL works as a fallback if the pretty URL goes down.
- (–) Users inherit `uv` as a transitive install (~20MB). For users
  who don't already do Python work, that's a real footprint addition
  for a tool they may try once and discard. Mitigation: `scan.sh`'s
  docstring is explicit that uv is the only persistent artifact.
- (–) First-run requires internet to bootstrap `uv` and resolve
  `openaca`. No offline path. Mitigation: documented manual install
  via `uv tool install openaca` for offline workflows.
- (–) Doesn't fully match single-binary CLI distributions on
  cold-machine friction (no runtime bootstrap at all). The remaining
  ~20% friction is the deferred decision in this ADR.
- (Watch) If issue tracker / community feedback indicates the
  uv-bootstrap step is itself a friction layer worth eliminating
  (e.g., users reporting confusion about uv's behavior, or
  enterprise-controlled environments where `uv install` is blocked),
  that's the trigger to escalate to pyapp or a single-binary rewrite.

## When to revisit

- Distribution friction is measured (via community reports, issue
  tracker mentions, design-partner feedback) as the remaining
  bottleneck after the cheap fixes have shipped.
- Fleet MDM deployment scale reaches a point where IT teams cite
  Python-toolchain dependency as the blocker for adoption.
- pyapp's release infrastructure becomes cheap enough (per-release
  CI cross-compile, automated code signing) that the cost-benefit
  shifts in its favor.
- A Rust/Go scanner rewrite becomes attractive for reasons other
  than distribution (e.g., performance on large fleets, agentic
  composition graph requiring a richer data model than Python
  comfortably supports).
