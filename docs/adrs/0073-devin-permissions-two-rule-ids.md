---
id: 0073
title: One Devin permissions list feeds two posture rule ids, split by entry
status: accepted
date: 2026-10-09
supersedes: null
superseded-by: null
---

## Context

Devin CLI's `permissions` block in `config.json` (user, project, project-local)
holds `allow`, `deny` and `ask` lists. One list mixes entries about different
subjects: `Exec(<prefix>)` and the bare tool name `exec` approve shell
commands; `mcp__<server>__<tool>`, `mcp__<server>__*` and `mcp__*` approve MCP
tools; `Read(…)`, `Write(…)`, `Fetch(…)` and the other tool names approve file
and network access.

`rule_id` is a policy gate key: `policy_cli` fails a finding whose id is absent
from `risk_gates.posture_rule_ids`, so every finding under one id is allowed or
denied together. The Codex spec records why shell approval and MCP approval
must not share one ([Two new rule IDs, not a re-pointed
one](../specs/codex-agent-kind.md#two-new-rule-ids-not-a-re-pointed-one)).

## Decision

Split the list by entry, reusing the existing ids:

| Allow entry | Reported under |
|---|---|
| `Exec(…)`, `exec` | `openaca-posture-command-policy-allow` |
| `mcp__*`, `mcp__<server>__*`, `mcp__<server>__<tool>` | `openaca-posture-mcp-auto-approve` |
| anything else | nothing; no rule covers it |

The lists merge across levels before reporting. An allow is reported only if it
is effective in its own chain of levels: a `deny` anywhere in that chain that
covers it suppresses it, and so does an `ask` at a higher-precedence level.
Devin documents both: "a deny rule always wins", and "a lower-level allow cannot
override an ask from a higher-precedence level". `config.json` is read as JSON
with comments.

## Alternatives considered

- **A Devin-specific rule id** — rejected. The id names what is approved, not
  who approves it. A team that vets MCP auto-approval would otherwise gate the
  same risk twice, once per runtime.
- **Everything under `mcp_auto_approve`** — rejected. A team allowing vetted
  MCP auto-approval would silently also allow unattended `git push`.
- **Report every allow without merging** — rejected. An allow its own deny
  overrides is not an exposure, and reporting it trains readers to ignore the
  rule.

## Consequences

One file can produce findings under two ids, and a team allowing one id does not
silently allow the other. `mcp_auto_approve` and `command_policy_allow` take
their `active_in` from the scanning agent rather than a hardcoded kind.

Organisation-level permissions come from Devin's service and are not read, so a
reported allow may still be overridden by a team deny the scan cannot see.

## When to revisit

If Devin adds a permission matcher whose subject is neither a shell command nor
an MCP tool and a posture rule exists for it, or if team settings become
readable from disk.
