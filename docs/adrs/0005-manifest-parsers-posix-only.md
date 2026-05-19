---
id: 0005
title: V0 manifest parsers are POSIX-only
status: accepted
date: 2026-05-08
supersedes: null
superseded-by: null
---

## Context

The manifest parsers in `tools/parsers/` (notably `mcp_json.py`'s
`_classify_command`) decide whether a manifest's `command` field
points at a known package launcher (`npx`, `uvx`, `uv`) or at an
arbitrary binary. The classification controls whether OpenACA emits a
PURL (and thus aliases an upstream CVE/GHSA) or falls to the
`mcp-stdio/binary:*` fallback.

PR #8 review surfaced four rounds of Windows-related findings on
consecutive commits:

1. Backslash paths (`C:\Program Files\nodejs\npx.cmd`) classify as
   binaries on POSIX runners because `Path` is OS-aware and doesn't
   split `\`.
2. `.cmd`/`.exe`/`.bat` extensions classify as binaries unless
   stripped explicitly. (Path.stem strips them for free, so partial.)
3. Uppercase variants (`NPX.CMD`, `C:\...\NPX.CMD`) miss the
   lowercase `cmd_class == "npx"` branch — Windows command resolution
   is case-insensitive.
4. Bare uppercase tokens (`{"command": "NPX"}`) — Windows resolves
   these via PATHEXT; same case-insensitivity question, but with no
   path or extension signal to distinguish from POSIX intent.

Each fix introduced a new finding on the next round. The fundamental
problem: `{"command": "NPX"}` is genuinely ambiguous — on Windows it
resolves case-insensitively to the npx launcher; on POSIX it's a
distinct binary at `/$PATH/NPX`. We can't tell which the manifest
targets from the JSON alone.

## Decision

**V0 manifest parsers target POSIX semantics only.** `_classify_command`
is `Path(command).stem` — case-sensitive, no backslash normalization,
no Windows extension handling, no PATHEXT-style bare-token recognition.

Practical consequences:

- `npx`, `/usr/local/bin/npx`, `npx.cmd` → classify as `npx` (the
  `.cmd` strip is a free side effect of `Path.stem`).
- `NPX`, `C:\Program Files\nodejs\npx.cmd`, `C:\...\NPX.CMD`,
  `/opt/NPX` → fall through to `mcp-stdio/binary:*` fallback.

POSIX correctness is preserved: `/opt/NPX` is a genuinely different
binary from `/opt/npx` and must not silently classify as the launcher.

## Alternatives considered

- **Always lowercase the classified stem.** Resolves Windows
  uppercase but introduces a false positive on POSIX `/opt/NPX`,
  silently emitting an npm PURL for what is actually a custom
  binary. Rejected — false positives in a security advisory database
  are worse than false negatives.

- **Lowercase only when Windows-shaped (backslash path or
  `.cmd`/`.exe`/`.bat`/`.ps1` extension).** Handles paths and
  extensions but still misses bare uppercase tokens like
  `{"command": "NPX"}` that Windows resolves via PATHEXT. Codex
  flagged this as a follow-up; we tried it and got the next finding.

- **Lowercase bare tokens against a known-launcher allow-list
  (`npx`/`uvx`/`uv`).** Resolves all three Windows shapes without
  the POSIX false positive. We implemented this in `2f38be5`. It
  works, but couples the classifier to the dispatcher's launcher
  set and ships Windows support that V0 doesn't otherwise need —
  no V0 plan or advisory targets Windows-specific paths. Reverted
  in `9c9f264` per the "no Windows in MVP" call.

- **Read an OS hint from a sibling field or out-of-band config.**
  No such field exists in any current MCP config convention; would
  require either inventing one or adding an OpenACA CLI flag. Out of
  V0 scope; logical place for V1.

## Consequences

What this enables:
- Simpler classifier: one line, no branching, no allow-lists.
- POSIX correctness without ambiguity. `/opt/NPX` and `CUSTOM`
  bare tokens stay as binaries — the conservative behavior for an
  advisory database.
- Stops the Codex back-and-forth: any Windows path / Windows ext
  / uppercase finding can be closed by pointing at this ADR.

What this costs:
- Real Windows MCP configs (backslash paths, uppercase tokens,
  `.cmd`/`.exe` extensions other than the `Path.stem`-strippable
  ones) will not detect package versions. They fall to binary
  fallback and OpenACA emits an `mcp-stdio/binary:*` identity instead
  of an aliased CVE/GHSA. Detection accuracy drops on Windows-only
  repositories.
- Mitigated by: (1) MCP/agent infrastructure ecosystem skews POSIX
  (most MCP servers documented and distributed for macOS/Linux);
  (2) Windows users with non-conventional configs aren't the V0
  detection focus; (3) binary fallback is correct, just less
  informative — no false positives.

What to watch for:
- If Codex (or any reviewer) re-suggests Windows handling on a
  parser change, point at this ADR rather than relitigating.
- If a real Windows-targeted advisory ends up in the V0 corpus
  and detection misses it, the gap becomes load-bearing.

## When to revisit

- V1 plan touches manifest parsers. Add an OS hint (`platform`
  config field, CLI flag, or per-manifest probe) and revisit the
  classifier with explicit knowledge of which OS the config
  targets.
- Telemetry (once OpenACA has any) shows real-world Windows MCP
  configurations being silently missed by detection.
- A downstream consumer (e.g., a scanner integration) reports
  cross-platform parity as a hard requirement.
- A real V0 advisory targets a Windows-specific component
  identity that the binary fallback can't represent.
