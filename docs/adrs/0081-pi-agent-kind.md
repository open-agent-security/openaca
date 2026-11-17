---
id: 0081
title: Register Pi as an agent kind, partial at both sources
status: accepted
date: 2026-11-17
supersedes: null
superseded-by: null
---

## Context

[`docs/specs/pi-agent-kind.md`](../specs/pi-agent-kind.md) audits Pi 1.5.2, read
from its shipped npm package. Pi's MCP servers and skills use formats OpenACA
already parses. Its other surfaces do not exist in any registered kind: an
extension is a TypeScript module that runs inside the Pi process, and a package
is an npm or git distribution of extensions, skills and prompt templates.

A session collector already names Pi sessions `pi`, and sessions join their BOM
by kind id. An extension can register MCP servers and skill paths at run time,
which no file records. Pi also loads a project's `.pi/` only after the user
trusts the project.

## Decision

1. Register the kind `pi`, singleton, rooted at `$PI_CODING_AGENT_DIR`, else
   `~/.pi/agent`.
2. Set the coverage baseline to `partial` at both sources. An extension can
   call `pi.registerMcpServer()` and return skill paths at run time, at either
   source, and no parser closes that.
3. Declared evidence is a Pi-owned file that loads: `.pi/settings.json`,
   `.pi/mcp.json`, an extension entry under `.pi/extensions/`, a
   `.pi/skills/**/SKILL.md`, a `.pi/prompts/*.md`, and the shared
   `.agents/skills/**/SKILL.md` (ADR-0058).
4. Refuse `--config-dir`. Pi reads `$HOME/.agents/skills` and the global npm
   root whatever the agent directory is. `$PI_CODING_AGENT_DIR` is honoured.
5. Gate the installed project layer on trust as Pi does. Skip it only for a
   recorded `false`, or no record and `defaultProjectTrust` `"never"`.

## Alternatives considered

- **`complete` at declared, as Codex is** — rejected. A repository's own
  `.pi/extensions/` can register servers in code. The multi-agent spec rule
  applies: a kind that permits code-authored composition cannot claim
  `complete`, whatever one project contains.
- **Name the kind `pi-coding-agent`, after the npm package** — rejected.
  Sessions join their BOM by kind id, and the session collector already names
  the kind `pi`.
- **Accept `--config-dir` and move `.agents` with it, as ADR-0059 does for
  Codex** — rejected. The global npm root does not move with any directory, and
  the environment variable already relocates the agent directory the way Pi
  does.
- **Compose the project layer only for a recorded `true`, as Codex's trust gate
  does** — rejected. Pi offers "Trust (this session only)" and extension
  handlers, which leave no record. Treating no record as untrusted would hide a
  project Pi loads.
- **Ignore trust and always compose the project layer** — rejected. A recorded
  `false` means Pi does not load the project, and reporting it misstates the
  agent.

## Consequences

A repository that holds only `.agents/skills/` now emits four BOMs: Cursor,
Codex, Devin and Pi. That is ADR-0058's stated cost, applied to a fourth reader.

An installed scan of an untrusted project reports no project components. The
scan records a note that says why.

Packages that are declared but not on disk, and packages declared in a
repository, are presence-only, and each records a gap in its scan.

## When to revisit

- Pi records which servers and skills its extensions registered in a file a
  scan can read.
- A Pi major release, which the audit's code-derived claims do not survive.
- Policy evidence needs Pi: add `pi` to `collect_policy_evidence`.
