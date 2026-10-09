---
id: 0070
title: Register Devin CLI as an agent kind, partial at both sources
status: accepted
date: 2026-10-09
supersedes: null
superseded-by: null
---

## Context

[`docs/specs/devin-cli-agent-kind.md`](../specs/devin-cli-agent-kind.md) audits
`devin` 3000.11.3. Its own files use formats OpenACA already parses: the
`mcpServers` map, `SKILL.md`, Claude Code's hook envelope and PascalCase events,
and Claude Code's and Agent Plugins' plugin manifests. By default it also
imports MCP servers, skills, commands and hooks from Claude Code, Cursor,
Windsurf and GitHub Copilot, each import gated by a `read_config_from` switch.

So most of the kind is reuse, and most of its risk is attribution: which files
prove a Devin CLI agent exists, and which only add to its composition.

## Decision

Register `devin-cli`, singleton, rooted at `$XDG_CONFIG_HOME/devin`, else
`~/.config/devin`, with the plugin store under `$XDG_DATA_HOME/devin/cli`, else
`~/.local/share/devin/cli`.

**Coverage is `partial` at both sources**, for different named gaps:

- *Installed*: managed org and personal plugins arrive from Devin's service and
  are only visible where cached, and an ACP client can register MCP servers at
  `session/new` that appear in no file. Neither closes by parsing.
- *Declared*: a repository cannot see the user's `read_config_from`, so whether
  the imported `.claude/*`, `.cursor/*`, `.windsurf/*` and `.github/skills` load
  is unknown. A project-level switch that names an import is honoured.

**Declared evidence** is Devin-owned files only, each also matched at any
depth: `.devin/mcp_config.json`, `.devin/config.json`, `.devin/hooks.v1.json`,
`.devin/skills/*/SKILL.md`, `.cognition/skills/*/SKILL.md`, `.devin/agents/*`,
`.devin-plugin/plugin.json`, and the shared `.agents/skills/*/SKILL.md`
(ADR-0058) and `.agents/agents/*` (ADR-0072). Content inside a realized plugin
is never evidence.

**Imports are composition, never evidence.** A tree holding only
`.claude/skills/` declares a Claude Code agent. The gitignored
`.devin/*.local.json` files are composition too, since their presence in a tree
is incidental.

**An imported MCP server and a same-named native one are both recorded**, each
with its own `source_manifest`. Devin's order between them is stated nowhere.
Devin's own layers merge by name as documented: project-local over project over
user.

**Installed plugins are presence-only**, read from the store's `lock.json`,
because the bundle layout inside the store could not be observed without a
signed-in account. A store plugin takes the occurrence-local `plugin/{name}`
identity, with no `marketplace`.

## Alternatives considered

- **`complete` at declared, as Codex is** — rejected. Codex's declared surface
  has no switch living outside the repository; Devin's imports do.
- **Treat imports as evidence** — rejected for the reason ADR-0052 gives for
  Cursor: every Claude-only repository would emit a phantom Devin BOM.
- **Pick an order for same-named imported and native servers** — rejected. A
  guessed order drops a server Devin may load, and the dropped one could be the
  vulnerable one.
- **Skip installed plugins until the store layout is verified** — rejected. A
  presence-only row tells a reader the plugin is installed; omitting it says it
  is not.

## Consequences

A repository that ships only `.agents/skills/` now emits three BOMs (Cursor,
Codex, Devin CLI), ADR-0058's stated cost applied to a third reader.

Devin's installed composition draws from four independent root groups, so the
normalizer carries a label for each foreign root it reads, and `--config-dir` is
refused (ADR-0071).

Store plugins carry no bundled contents until the layout is verified, so a
vulnerable component shipped inside one is not inventoried.

## When to revisit

- The plugin store layout is observed: read bundle contents, and set
  `marketplace` if the store records a resolved source.
- Devin documents the order between imported and native same-named servers.
- A Devin major release, which the audit's implementation-derived claims do not
  survive.
