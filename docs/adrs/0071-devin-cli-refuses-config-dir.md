---
id: 0071
title: Devin CLI refuses --config-dir
status: accepted
date: 2026-10-09
supersedes: null
superseded-by: null
---

## Context

ADR-0054 grants a root override only to a kind for which naming a root fully
specifies the target. ADR-0059 kept Codex's override by moving its one
home-derived companion, `.agents/skills`, to `<dir>/../.agents`, which is
faithful because `.codex` and `.agents` are siblings on a real endpoint.

An installed Devin CLI composes from four independent groups:

1. the config root, moved by `$XDG_CONFIG_HOME`;
2. the data root holding the plugin store, moved by `$XDG_DATA_HOME`, a
   different variable;
3. `$HOME/.agents/skills`, which neither variable moves;
4. other runtimes' home trees: `~/.claude`, `~/.claude.json`, `~/.codeium`,
   `~/.copilot`.

## Decision

`devin-cli` declares no relocatable root. `--config-dir` with `--kind
devin-cli` is rejected with a `root_override_refusal` naming the reason. An
installed scan always resolves Devin's roots from the environment and the
invoking user's home.

## Alternatives considered

- **Relocate the companions as ADR-0059 does** — rejected. A sibling rule would
  have to invent where `~/.local/share` sits relative to `~/.config`, and where
  `~/.claude` and `~/.codeium` sit relative to both. Each guess is a way to
  stitch two homes into one composition that the output cannot distinguish from
  a correct scan.
- **Honour the flag for the config root only** — rejected. That is exactly the
  stitched composition ADR-0054 refuses.

## Consequences

Scanning a Devin tree that is not the invoking user's home is unsupported, the
same unserved need ADR-0054 records for Cursor. Tests root a fixture by faking
the home directory and the two XDG variables.

## When to revisit

When ADR-0054's deferred "treat this directory as home" override is designed,
which would move every group together.
