---
id: 0083
title: OpenCode's permissions feed no posture rule, and the skill rule does not apply
status: accepted
date: 2026-11-17
supersedes: null
superseded-by: null
---

## Context

OpenCode's `permission` key holds `allow`, `ask` and `deny` rules per tool, such
as `{"bash": "allow"}` or `{"github_*": "ask"}`. Devin's `permissions` list feeds
`mcp_auto_approve` and `command_policy_allow` (ADR-0073), and a reviewer will
ask for the same here.

Those rules report an opt-in to unattended use, against a runtime that confirms
by default. OpenCode does not confirm by default. The ruleset of every built-in
agent starts with `"*": "allow"` (`packages/opencode/src/agent/agent.ts`).

`skill_capability` reads a skill's `allowed-tools`. OpenCode reads only `name`
and `description` from a skill (`packages/opencode/src/skill/index.ts`).

## Decision

1. **`mcp_auto_approve` and `command_policy_allow` are not on OpenCode's
   allowlist.** No collector reads `permission`.
2. **`skill_capability` is not on OpenCode's allowlist.**

Reporting OpenCode's default needs a rule about the absence of a confirmation.
That is a new rule id, with its own ADR.

## Alternatives considered

- **Report `permission` allows, as ADR-0073 does for Devin** — rejected. An
  explicit `allow` changes nothing in OpenCode, so it would be reported. A
  configuration with no `permission` key, the most permissive state, would not.
- **Report every MCP server and `bash` as auto-approved unless a rule asks** —
  rejected under these ids. It reports the runtime's default, not a declaration,
  and it would put a finding on every OpenCode server. That is a different rule.
- **Keep `skill_capability`, as Cursor and Codex do** — rejected. OpenCode does
  not read `allowed-tools`, so a finding would report access the skill does not
  get.

## Consequences

An OpenCode scan reports no auto-approval finding. A reader must not take that
as a sign of confirmation. The spec's Posture section says so.

## When to revisit

- When a rule for runtimes that allow by default is written.
- When OpenCode changes its default ruleset or reads `allowed-tools`.
