---
id: 0081
title: Register OpenCode as an agent kind, partial at both sources
status: accepted
date: 2026-11-17
supersedes: null
superseded-by: null
---

## Context

[`docs/specs/opencode-agent-kind.md`](../specs/opencode-agent-kind.md) audits the
source of OpenCode v1.19.5. OpenCode merges many JSON-with-comments files into
one configuration. MCP servers, npm plugins, agents and commands come from that
configuration. More agents, commands, plugins and skills come from files in each
configuration directory. A session collector already names OpenCode sessions
`opencode`, and sessions join their BOM by kind id.

Three choices have a plausible alternative. Which sources lower coverage. How a
server that several files configure becomes components. Whether a named root can
replace OpenCode's own.

## Decision

1. **Register `opencode`**, singleton, rooted at `$XDG_CONFIG_HOME/opencode`,
   else `~/.config/opencode`. Its built-ins are the seven `native` agents and the
   `customize-opencode` skill of v1.19.5, listed on the root (ADR-0076).
2. **Coverage is `partial` at both sources.** At both, a plugin is code that can
   add tools and change the configuration. Installed also misses MCP servers
   registered at run time, remote configuration, and the process environment.
   None of these closes by parsing.
3. **MCP servers merge by name, as OpenCode merges them.** One component per
   name, from the merged entry, attributed to the highest-precedence file that
   sets `type`. `enabled` is always stated, and a disabled server stays in the
   inventory (ADR-0055).
4. **Declared evidence** is OpenCode-owned files at any depth: `opencode.json`,
   `opencode.jsonc`, the `.opencode/` agent, mode, command, plugin and skill
   files, and the shared `.agents/skills/` (ADR-0058). `.claude/skills/` is
   composition only.
5. **`--config-dir` is refused** (ADR-0054). OpenCode composes from its config
   root, a plugin cache on another XDG variable, `~/.opencode`, other runtimes'
   skill roots in the home directory, and a managed directory.

## Alternatives considered

- **`complete` at declared, as Codex is** — rejected. A repository can ship a
  plugin file, and the plugin's `config` hook can add servers and agents that no
  file records. Coverage cannot vary by how a project happens to be written.
- **Each file's MCP entry as its own occurrence, as for Devin's imports** —
  rejected. OpenCode states its merge: one deep merge in a known order. A
  `{"enabled": false}` entry has no command and no URL, so as an occurrence it
  says nothing, and the server it switches off would read as enabled.
- **Drop a disabled MCP server** — rejected for the reason ADR-0055 gives for
  Codex: the state is in a file, and withholding a fact we can read is the
  mirror error of fabricating one.
- **Accept `--config-dir`, moving `~/.opencode` with it, as ADR-0059 does for
  Codex** — rejected. ADR-0059 moves one sibling. OpenCode has five roots, and
  two of them are other runtimes' trees.

## Consequences

A repository that ships only `.agents/skills/` now emits four BOMs: Cursor,
Codex, Devin and OpenCode. That is ADR-0058's stated cost, applied to a fourth
reader.

The installed composition reads a home directory and a system directory beside
the config root, so the normalizer carries a label for each of them.

## When to revisit

- When OpenCode records run-time MCP servers or remote configuration in a file.
- When a release changes the built-in agents or the merge order.
